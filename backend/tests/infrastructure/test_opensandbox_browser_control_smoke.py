"""真实 OpenSandbox Browser 画面回路冒烟测试（默认跳过）。

显式启用：
``AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1 uv run pytest
tests/infrastructure/test_opensandbox_browser_control_smoke.py -q``。

测试只访问 Sandbox 内合成页面，不调用模型或公网。它先用 Playwright MCP 打开合成页，再经产品
``AgentBrowserPanelView``/noVNC、平台 ticket WebSocket relay 向同一 Sandbox Chromium 注入键盘输入，
最后由同一 generation 的 Playwright MCP 读取变化。直接 RFB 握手只保留为诊断，不充当人工输入证据。
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp import ClientSession
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol

from literature_agent.api.agent_browser import _browser_ticket_from_subprotocols
from literature_agent.infrastructure.agent.browser_gateway import (
    BrowserWebSocketTarget,
    bridge_vnc_websocket,
    connect_browser_upstream,
)
from literature_agent.infrastructure.agent.browser_ticket import HmacBrowserTicketIssuer
from literature_agent.infrastructure.agent.opensandbox_backend import (
    BrowserWebSocketEndpoint,
    OpenSandboxProvider,
)
from literature_agent.infrastructure.config import Settings

_real_opensandbox = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_OPENSANDBOX_BROWSER_TESTS") != "1",
    reason=("真实 OpenSandbox Browser 冒烟测试需显式启用（AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1）"),
)

_REPOSITORY_ROOT = Path(__file__).parents[3]

_SYNTHETIC_SERVER = b"""\
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path('/workspace/browser-control-smoke')
root.mkdir(parents=True, exist_ok=True)
(root / 'index.html').write_text(
    '<!doctype html><title>browser-control-smoke</title>'
    '<style>html,body{margin:0;width:100%;height:100%}'
    '#manual{position:fixed;inset:0;width:100%;height:100%;box-sizing:border-box;'
    'font-size:32px;padding:48px}</style>'
    '<label for="manual">same-sandbox-before</label>'
    '<input id="manual" autofocus aria-label="manual marker" '
    'placeholder="same-sandbox-before" '
    'oninput="document.title=this.value">'
    '<script>document.getElementById(&quot;manual&quot;).focus()</script>',
    encoding='utf-8',
)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(root), **kwargs)

ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
"""

_SYNTHETIC_READINESS = b"""\
import http.client
import sys
import time

deadline = time.monotonic() + 10.0
delay = 0.05
while time.monotonic() < deadline:
    connection = http.client.HTTPConnection('127.0.0.1', 8765, timeout=0.5)
    try:
        connection.request('GET', '/')
        response = connection.getresponse()
        body = response.read(4096)
        if response.status == 200 and b'same-sandbox-before' in body:
            raise SystemExit(0)
    except OSError:
        pass
    finally:
        connection.close()
    time.sleep(delay)
    delay = min(delay * 2, 0.25)
raise SystemExit('synthetic browser service did not become ready')
"""


def test_synthetic_browser_scripts_compile_and_keep_expected_page_contract() -> None:
    compile(_SYNTHETIC_SERVER, "serve_browser_control_smoke.py", "exec")
    compile(_SYNTHETIC_READINESS, "check_browser_control_smoke.py", "exec")
    source = _SYNTHETIC_SERVER.decode("utf-8")
    assert "same-sandbox-before" in source
    assert 'id="manual" autofocus' in source
    assert "position:fixed;inset:0" in source
    assert "document.title=this.value" in source


def test_no_vnc_ui_smoke_assets_reuse_the_production_browser_viewer() -> None:
    """真实 UI Smoke 必须装配生产组件，不能另写一个等价 RFB 客户端冒充 noVNC。"""
    harness = (_REPOSITORY_ROOT / "web/e2e/browser-control-harness.tsx").read_text(encoding="utf-8")
    runner = (_REPOSITORY_ROOT / "web/e2e/browser-control-ui-smoke.mjs").read_text(encoding="utf-8")

    assert "AgentBrowserPanelView" in harness
    assert "new RFB" not in harness
    assert "当前研究会话的交互式浏览器画面" in runner
    assert "完成操作" in runner


class _CommandBackend(Protocol):
    def execute(self, command: str) -> Any: ...


async def _start_synthetic_server(backend: _CommandBackend) -> None:
    """启动合成服务并在 Sandbox 内确定性等待 HTTP readiness。"""
    started = await asyncio.to_thread(
        backend.execute,
        "nohup /opt/research-agent-venv/bin/python "
        "/workspace/serve_browser_control_smoke.py "
        "</dev/null >/tmp/browser-control-smoke.log 2>&1 &",
    )
    assert started.exit_code == 0
    ready = await asyncio.to_thread(
        backend.execute,
        "/opt/research-agent-venv/bin/python /workspace/check_browser_control_smoke.py",
    )
    if ready.exit_code == 0:
        return
    log = await asyncio.to_thread(
        backend.execute,
        "tail -c 4096 /tmp/browser-control-smoke.log",
    )
    pytest.fail(f"Sandbox 合成服务未就绪；安全日志：{str(log.output)[:4096]}")


def _direct_mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """真实本地 Smoke 不继承宿主代理；不修改进程或用户环境。"""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=True,
        trust_env=False,
    )


def test_direct_mcp_http_client_disables_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:1")
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    result = _direct_mcp_http_client(headers={"X-Test": "safe"})

    assert result is sentinel
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is True


async def test_synthetic_server_waits_for_readiness_and_reads_log_only_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, exit_code: int, stdout: str = "") -> None:
            self.exit_code = exit_code
            self.stdout = stdout
            self.stderr = ""

    class Backend:
        def __init__(self) -> None:
            self.commands: list[str] = []
            self.results = [Result(0), Result(0)]

        def execute(self, command: str) -> Result:
            self.commands.append(command)
            return self.results.pop(0)

    async def direct_to_thread(function: Any, *args: Any) -> Any:
        return function(*args)

    backend = Backend()
    monkeypatch.setattr(asyncio, "to_thread", direct_to_thread)
    await _start_synthetic_server(backend)

    assert len(backend.commands) == 2
    assert "nohup /opt/research-agent-venv/bin/python" in backend.commands[0]
    assert "check_browser_control_smoke.py" in backend.commands[1]
    assert all("tail -c" not in command for command in backend.commands)


async def test_synthetic_server_failure_reads_only_bounded_safe_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, exit_code: int, output: str = "") -> None:
            self.exit_code = exit_code
            self.output = output

    class Backend:
        def __init__(self) -> None:
            self.commands: list[str] = []
            self.results = [Result(0), Result(1), Result(0, "safe" * 2000)]

        def execute(self, command: str) -> Result:
            self.commands.append(command)
            return self.results.pop(0)

    async def direct_to_thread(function: Any, *args: Any) -> Any:
        return function(*args)

    backend = Backend()
    monkeypatch.setattr(asyncio, "to_thread", direct_to_thread)

    with pytest.raises(pytest.fail.Exception) as raised:
        await _start_synthetic_server(backend)

    assert backend.commands[-1] == "tail -c 4096 /tmp/browser-control-smoke.log"
    assert "safe" in str(raised.value)
    assert len(str(raised.value).removeprefix("Sandbox 合成服务未就绪；安全日志：")) == 4096


@asynccontextmanager
async def _playwright_session(
    endpoint: str, headers: dict[str, str]
) -> AsyncIterator[ClientSession]:
    parts = urlsplit(endpoint)
    url = urlunsplit((parts.scheme, parts.netloc, f"{parts.path.rstrip('/')}/mcp", parts.query, ""))
    client = MultiServerMCPClient(
        {
            "playwright": {
                "transport": "streamable_http",
                "url": url,
                "headers": headers,
                "httpx_client_factory": _direct_mcp_http_client,
            }
        }
    )
    async with client.session("playwright") as session:
        yield session


async def _rfb_banner(target: BrowserWebSocketEndpoint) -> bytes:
    upstream = await websocket_connect(
        target.url,
        additional_headers=target.headers,
        subprotocols=[Subprotocol("binary")],
        compression=None,
        proxy=None,
        open_timeout=10,
        close_timeout=5,
        max_size=1024 * 1024,
    )
    try:
        banner = b""
        while len(banner) < 12:
            message = await asyncio.wait_for(upstream.recv(), timeout=10)
            assert isinstance(message, bytes)
            banner += message
        return banner[:12]
    finally:
        await upstream.close()


async def _rfb_security_types(target: BrowserWebSocketEndpoint) -> tuple[int, ...]:
    """只用于诊断固定镜像能否在不把密码交给前端的情况下由 noVNC 接管。"""
    upstream = await websocket_connect(
        target.url,
        additional_headers=target.headers,
        subprotocols=[Subprotocol("binary")],
        compression=None,
        proxy=None,
        open_timeout=10,
        close_timeout=5,
        max_size=1024 * 1024,
    )
    try:
        banner = await asyncio.wait_for(upstream.recv(), timeout=10)
        assert isinstance(banner, bytes) and banner.startswith(b"RFB ")
        await upstream.send(b"RFB 003.008\n")
        response = await asyncio.wait_for(upstream.recv(), timeout=10)
        assert isinstance(response, bytes) and response
        count = response[0]
        return tuple(response[1 : count + 1])
    finally:
        await upstream.close()


class _RelayWebSocket:
    """把 websockets server connection 适配到生产 ``bridge_vnc_websocket``。"""

    def __init__(self, connection: ServerConnection) -> None:
        self._connection = connection

    async def send_bytes(self, data: bytes) -> None:
        await self._connection.send(data)

    async def receive(self) -> dict[str, object]:
        try:
            data = await self._connection.recv()
        except ConnectionClosed:
            return {"type": "websocket.disconnect"}
        return {"type": "websocket.receive", "bytes": data}


@asynccontextmanager
async def _browser_ticket_relay(
    target: BrowserWebSocketEndpoint,
    *,
    ticket: str,
) -> AsyncIterator[tuple[str, list[str]]]:
    """测试 relay 复用生产 ticket 解析、上游连接与有界双向 bridge。"""

    observations: list[str] = []

    async def handler(connection: ServerConnection) -> None:
        observations.append("client_connected")
        try:
            header = connection.request.headers.get("Sec-WebSocket-Protocol", "")
            if _browser_ticket_from_subprotocols(header) != ticket:
                observations.append("ticket_rejected")
                await connection.close(code=4403)
                return
            observations.append("ticket_accepted")
            upstream = await connect_browser_upstream(
                BrowserWebSocketTarget(url=target.url, headers=target.headers)
            )
            observations.append("upstream_connected")
            await bridge_vnc_websocket(
                _RelayWebSocket(connection),
                upstream,
                is_current=lambda: asyncio.sleep(0, result=True),
                total_timeout_seconds=60,
            )
            observations.append("bridge_completed")
        except Exception as exc:
            observations.append(f"bridge_failed:{type(exc).__name__}:{exc}")
            raise

    async with serve(
        handler,
        "127.0.0.1",
        0,
        subprotocols=[Subprotocol("binary")],
        compression=None,
        max_size=1024 * 1024,
    ) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", observations


def _free_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_for_harness(url: str) -> None:
    deadline = asyncio.get_running_loop().time() + 20
    async with httpx.AsyncClient(trust_env=False) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get(url, timeout=1)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise TimeoutError("Vite Browser control harness 未就绪")


async def _run_product_no_vnc_input(
    target: BrowserWebSocketEndpoint,
    *,
    marker: str,
) -> dict[str, object]:
    """由生产 BrowserViewer/noVNC 经 ticket relay 向远端 Chromium 注入 marker。"""
    ticket = HmacBrowserTicketIssuer(b"phase6-browser-ticket-secret-32b").issue(
        "phase6-browser-smoke-control", 1
    )
    async with _browser_ticket_relay(target, ticket=ticket) as (
        relay_target,
        relay_observations,
    ):
        port = _free_tcp_port()
        harness_path = "/e2e/browser-control-harness.html"
        harness_url = (
            f"http://127.0.0.1:{port}{harness_path}"
            f"?ticket={ticket}&viewUrl=/api/v1/agent-browser-controls/view"
        )
        process = await asyncio.create_subprocess_exec(
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--strictPort",
            cwd=_REPOSITORY_ROOT / "web",
            env={**os.environ, "VITE_API_PROXY_TARGET": relay_target},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await _wait_for_harness(harness_url)
            completed = await asyncio.create_subprocess_exec(
                "node",
                str(_REPOSITORY_ROOT / "web/e2e/browser-control-ui-smoke.mjs"),
                harness_url,
                marker,
                cwd=_REPOSITORY_ROOT / "web",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(completed.communicate(), timeout=40)
            assert completed.returncode == 0, (
                stderr.decode("utf-8", errors="replace")
                + f"\nrelay_observations={relay_observations!r}"
            )
            import json

            return json.loads(stdout)
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()


@_real_opensandbox
async def test_real_opensandbox_vnc_and_playwright_share_one_sandbox() -> None:
    defaults = Settings()
    provider = OpenSandboxProvider(
        domain=os.environ.get("AGENT_RESEARCH_SANDBOX_DOMAIN", defaults.research_sandbox_domain),
        protocol=os.environ.get(
            "AGENT_RESEARCH_SANDBOX_PROTOCOL", defaults.research_sandbox_protocol
        ),
        api_key=os.environ.get("AGENT_RESEARCH_SANDBOX_API_KEY") or None,
    )
    image_ref = os.environ.get("AGENT_RESEARCH_SANDBOX_IMAGE", defaults.research_sandbox_image)
    backend = await provider.create(
        image_ref=image_ref,
        ttl_seconds=300,
        cpu=1,
        memory_mib=2048,
        network_enabled=False,
        metadata={
            "test": "phase6-slice3-browser",
            "session_id": "browser-smoke-session",
            "generation": "1",
        },
    )
    sandbox_id = backend.id
    try:
        uploaded = await asyncio.to_thread(
            backend.upload_files,
            [
                ("/workspace/serve_browser_control_smoke.py", _SYNTHETIC_SERVER),
                ("/workspace/check_browser_control_smoke.py", _SYNTHETIC_READINESS),
            ],
        )
        assert all(item.error is None for item in uploaded)
        await _start_synthetic_server(backend)

        browser_target = await provider.get_browser_websocket_target(sandbox_id, port=6080)
        assert await _rfb_banner(browser_target) in {
            b"RFB 003.008\n",
            b"RFB 003.007\n",
            b"RFB 003.003\n",
        }
        assert 1 in await _rfb_security_types(browser_target), (
            "固定镜像必须提供 RFB None security；不得把 VNC 密码交给公共前端"
        )

        await asyncio.to_thread(backend.prepare_mcp_service, "playwright")
        endpoint, headers, authority = await asyncio.to_thread(backend.get_mcp_endpoint, 8931)
        await asyncio.to_thread(
            backend.configure_mcp_service,
            "playwright",
            allowed_host=authority,
        )
        async with _playwright_session(endpoint, headers) as session:
            navigated = await session.call_tool(
                "browser_navigate", {"url": "http://127.0.0.1:8765"}
            )
            snapshot = await session.call_tool("browser_snapshot", {})
            assert navigated.isError is not True
            assert snapshot.isError is not True
            snapshot_text = "\n".join(str(getattr(item, "text", "")) for item in snapshot.content)
            assert "same-sandbox-before" in snapshot_text

            marker = "same-sandbox-manual-input"
            browser_target = await provider.get_browser_websocket_target(sandbox_id, port=6080)
            ui_result = await _run_product_no_vnc_input(browser_target, marker=marker)
            assert ui_result == {"injected": marker, "manualControlEnded": True}

            snapshot = await session.call_tool("browser_snapshot", {})
            assert snapshot.isError is not True
            snapshot_text = "\n".join(str(getattr(item, "text", "")) for item in snapshot.content)
            assert marker in snapshot_text

        # Playwright 操作后，同一 sandbox_id 的固定 websockify endpoint 仍完成 RFB 握手。
        assert backend.id == sandbox_id
        browser_target = await provider.get_browser_websocket_target(sandbox_id, port=6080)
        assert await _rfb_banner(browser_target) in {
            b"RFB 003.008\n",
            b"RFB 003.007\n",
            b"RFB 003.003\n",
        }
    finally:
        try:
            await asyncio.to_thread(backend.close)
        finally:
            await provider.destroy(sandbox_id)
