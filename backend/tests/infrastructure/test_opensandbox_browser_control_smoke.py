"""真实 OpenSandbox Browser 画面回路冒烟测试（默认跳过）。

显式启用：
``AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1 uv run pytest
tests/infrastructure/test_opensandbox_browser_control_smoke.py -q``。

测试只访问 Sandbox 内合成页面，不调用模型或公网。它证明固定 websockify 经 OpenSandbox server proxy
可以完成 RFB 握手，且 Playwright MCP 操作来自同一个 Sandbox 实例；不把该证据夸大为 noVNC 键鼠
人工输入 E2E。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp import ClientSession
from websockets.asyncio.client import connect as websocket_connect
from websockets.typing import Subprotocol

from literature_agent.infrastructure.agent.opensandbox_backend import (
    BrowserWebSocketEndpoint,
    OpenSandboxProvider,
)
from literature_agent.infrastructure.config import Settings

_real_opensandbox = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_OPENSANDBOX_BROWSER_TESTS") != "1",
    reason=(
        "真实 OpenSandbox Browser 冒烟测试需显式启用"
        "（AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1）"
    ),
)

_SYNTHETIC_SERVER = b"""\
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path('/workspace/browser-control-smoke')
root.mkdir(parents=True, exist_ok=True)
(root / 'index.html').write_text(
    '<!doctype html><title>browser-control-smoke</title>'
    '<h1>same-sandbox-before</h1>'
    '<button id="mark" '
    'onclick="this.textContent=&quot;same-sandbox-after&quot;">mark</button>',
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
    assert "same-sandbox-after" in source
    assert 'onclick="this.textContent=&quot;same-sandbox-after&quot;"' in source


class _CommandBackend(Protocol):
    def execute(self, command: str) -> Any: ...


async def _start_synthetic_server(backend: _CommandBackend) -> None:
    """启动合成服务并在 Sandbox 内确定性等待 HTTP readiness。"""
    started = await asyncio.to_thread(
        backend.execute,
        "nohup python /workspace/serve_browser_control_smoke.py "
        "</dev/null >/tmp/browser-control-smoke.log 2>&1 &",
    )
    assert started.exit_code == 0
    ready = await asyncio.to_thread(
        backend.execute,
        "python /workspace/check_browser_control_smoke.py",
    )
    if ready.exit_code == 0:
        return
    log = await asyncio.to_thread(
        backend.execute,
        "tail -c 4096 /tmp/browser-control-smoke.log",
    )
    pytest.fail(
        "Sandbox 合成服务未就绪；安全日志："
        f"{str(log.output)[:4096]}"
    )


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
    assert "nohup python" in backend.commands[0]
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
    url = urlunsplit(
        (parts.scheme, parts.netloc, f"{parts.path.rstrip('/')}/mcp", parts.query, "")
    )
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


@_real_opensandbox
async def test_real_opensandbox_vnc_and_playwright_share_one_sandbox() -> None:
    defaults = Settings()
    provider = OpenSandboxProvider(
        domain=os.environ.get(
            "AGENT_RESEARCH_SANDBOX_DOMAIN", defaults.research_sandbox_domain
        ),
        protocol=os.environ.get(
            "AGENT_RESEARCH_SANDBOX_PROTOCOL", defaults.research_sandbox_protocol
        ),
        api_key=os.environ.get("AGENT_RESEARCH_SANDBOX_API_KEY") or None,
    )
    image_ref = os.environ.get(
        "AGENT_RESEARCH_SANDBOX_IMAGE", defaults.research_sandbox_image
    )
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

        browser_target = await provider.get_browser_websocket_target(
            sandbox_id, port=6080
        )
        assert await _rfb_banner(browser_target) in {
            b"RFB 003.008\n",
            b"RFB 003.007\n",
            b"RFB 003.003\n",
        }

        await asyncio.to_thread(backend.prepare_mcp_service, "playwright")
        endpoint, headers, authority = await asyncio.to_thread(
            backend.get_mcp_endpoint, 8931
        )
        await asyncio.to_thread(
            backend.configure_mcp_service,
            "playwright",
            allowed_host=authority,
        )
        async with _playwright_session(endpoint, headers) as session:
            navigated = await session.call_tool(
                "browser_navigate", {"url": "http://127.0.0.1:8765"}
            )
            clicked = await session.call_tool(
                "browser_click", {"target": "button#mark"}
            )
            snapshot = await session.call_tool("browser_snapshot", {})
            assert navigated.isError is not True
            assert clicked.isError is not True
            assert snapshot.isError is not True
            snapshot_text = "\n".join(
                str(getattr(item, "text", "")) for item in snapshot.content
            )
            assert "same-sandbox-after" in snapshot_text

        # Playwright 操作后，同一 sandbox_id 的固定 websockify endpoint 仍完成 RFB 握手。
        assert backend.id == sandbox_id
        browser_target = await provider.get_browser_websocket_target(
            sandbox_id, port=6080
        )
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
