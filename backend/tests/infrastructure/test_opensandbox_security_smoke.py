"""显式 opt-in 的本地 OpenSandbox 安全/资源 Smoke。"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit, urlunsplit

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp import ClientSession
from opensandbox.exceptions import SandboxApiException

from literature_agent.infrastructure.agent.opensandbox_backend import OpenSandboxProvider

pytestmark = pytest.mark.opensandbox_security

_RUN = os.getenv("AGENT_RUN_OPENSANDBOX_SECURITY_TESTS") == "1"
_IMAGE = (
    "agent-service/research-agent-sandbox@sha256:"
    "8ded4a3cfb5603efac3e297a09f79f4bdef798379728eeb96d563ae8f99f40d1"
)


def _assert_ok(backend, command: str) -> str:
    response = backend.execute(command)
    assert response.exit_code == 0, response.output
    return response.output


@asynccontextmanager
async def _mcp_session(
    endpoint: str,
    headers: dict[str, str],
) -> AsyncIterator[ClientSession]:
    parts = urlsplit(endpoint)
    url = urlunsplit(
        (parts.scheme, parts.netloc, f"{parts.path.rstrip('/')}/mcp/", parts.query, "")
    )
    client = MultiServerMCPClient(
        {
            "arxiv-search": {
                "transport": "streamable_http",
                "url": url,
                "headers": headers,
            }
        }
    )
    async with client.session("arxiv-search") as session:
        yield session


@pytest.mark.skipif(not _RUN, reason="需要显式启用本地 OpenSandbox 安全 Smoke")
async def test_real_sandbox_is_non_root_secret_free_bounded_and_default_deny() -> None:
    provider = OpenSandboxProvider(
        domain=os.getenv("AGENT_RESEARCH_SANDBOX_DOMAIN", "127.0.0.1:8080"),
        protocol=os.getenv("AGENT_RESEARCH_SANDBOX_PROTOCOL", "http"),
        api_key=os.getenv("AGENT_RESEARCH_SANDBOX_API_KEY") or None,
    )
    backend = await provider.create(
        image_ref=os.getenv("AGENT_RESEARCH_SANDBOX_IMAGE", _IMAGE),
        ttl_seconds=300,
        cpu=1,
        memory_mib=2048,
        network_enabled=False,
        metadata={"smoke": "phase6-slice6"},
    )
    try:
        assert _assert_ok(backend, "id -u").strip() != "0"
        assert _assert_ok(backend, "cat /sys/fs/cgroup/memory.max").strip() == str(
            2048 * 1024 * 1024
        )
        assert _assert_ok(backend, "cat /sys/fs/cgroup/pids.max").strip() == "256"
        cpu_max = _assert_ok(backend, "cat /sys/fs/cgroup/cpu.max").split()
        assert len(cpu_max) == 2 and int(cpu_max[0]) == int(cpu_max[1])

        forbidden_mounts = _assert_ok(
            backend,
            "for p in /var/run/docker.sock /var/run/postgresql "
            "/home/xubin/Projects/agent-service; do test ! -e \"$p\" || exit 41; done",
        )
        assert forbidden_mounts == ""
        environment = _assert_ok(backend, "env")
        for secret_name in (
            "AGENT_RESEARCH_MODEL_API_KEY",
            "AGENT_CHAT_API_KEY",
            "AGENT_EMBEDDING_API_KEY",
            "AGENT_DATABASE_URL",
            "AGENT_REDIS_URL",
            "AGENT_RESEARCH_SANDBOX_API_KEY",
        ):
            assert f"{secret_name}=" not in environment

        # loopback 只能访问 Sandbox 自己刚启动的服务。
        loopback = _assert_ok(
            backend,
            "python3 -m http.server 8765 --bind 127.0.0.1 >/tmp/http.log 2>&1 & "
            "pid=$!; sleep 1; python3 -c \"import urllib.request; "
            "urllib.request.urlopen('http://127.0.0.1:8765/', timeout=2).read()\"; "
            "kill $pid; wait $pid 2>/dev/null || true",
        )
        assert loopback == ""

        blocked_commands = (
            "timeout 4 bash -c 'exec 3<>/dev/tcp/example.com/80'",
            "python3 -c \"import urllib.request; "
            "urllib.request.urlopen('https://example.com', timeout=3)\"",
            "timeout 5 node -e \"fetch('https://example.com')"
            ".then(()=>process.exit(0)).catch(()=>process.exit(9))\"",
            "python3 -c \"import urllib.request; "
            "urllib.request.urlopen('http://169.254.169.254/latest/meta-data/', timeout=3)\"",
            "python3 -c \"import socket,struct;"
            "r=open('/proc/net/route').readlines()[1].split();"
            "g=socket.inet_ntoa(struct.pack('<L',int(r[2],16)));"
            "socket.create_connection((g,8080),timeout=3)\"",
        )
        for command in blocked_commands:
            response = backend.execute(command, timeout=5)
            assert response.exit_code != 0

        # Chromium 也位于同一 network namespace，不能取得外部测试页正文。
        browser = backend.execute(
            "timeout 8 chromium --headless --no-sandbox --disable-gpu "
            "--dump-dom https://example.com/",
            timeout=10,
        )
        assert "Example Domain" not in browser.output

        # Playwright 通过同一 CDP 控制同一 Chromium；网络拒绝来自容器 namespace。
        playwright = backend.execute(
            "timeout 8 node -e \"const {chromium}=require("
            "'/opt/research-agent-mcp-node/node_modules/playwright');"
            "(async()=>{const b=await chromium.connectOverCDP('http://127.0.0.1:9222');"
            "const p=await b.newPage();try{await p.goto('https://example.com',"
            "{timeout:3000});process.exit(0)}catch(_){process.exit(9)}})()\"",
            timeout=10,
        )
        assert playwright.exit_code != 0

        # 固定 Search MCP 能启动，但真实搜索无法越过 default-deny。
        backend.prepare_mcp_service("arxiv-search")
        endpoint, headers, authority = backend.get_mcp_endpoint(8932)
        backend.configure_mcp_service("arxiv-search", allowed_host=authority)
        endpoint_host = urlsplit(endpoint).hostname
        assert endpoint_host is not None
        with pytest.MonkeyPatch.context() as monkeypatch:
            no_proxy = f"127.0.0.1,localhost,{endpoint_host}"
            monkeypatch.setenv("NO_PROXY", no_proxy)
            monkeypatch.setenv("no_proxy", no_proxy)
            for name in (
                "ALL_PROXY",
                "all_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "HTTPS_PROXY",
                "https_proxy",
            ):
                monkeypatch.delenv(name, raising=False)
            async with _mcp_session(endpoint, headers) as session:
                try:
                    async with asyncio.timeout(20):
                        search = await session.call_tool(
                            "search_papers",
                            {"query": "phase6-egress-smoke", "max_results": 1},
                        )
                except TimeoutError:
                    pass
                else:
                    assert search.isError is True

        bounded = backend.execute(
            "python3 -c \"print('x' * 70000)\"",
            timeout=5,
        )
        assert bounded.truncated is True
        assert len(bounded.output.encode()) <= 64 * 1024
    finally:
        with suppress(Exception):
            await provider.destroy(backend.id)

    # destroy 是幂等补偿边界：远端不存在也视为完成。
    await provider.destroy(backend.id)


@pytest.mark.skipif(not _RUN, reason="需要显式启用本地 OpenSandbox 安全 Smoke")
async def test_real_sandbox_ttl_expires_and_cleanup_remains_idempotent() -> None:
    provider = OpenSandboxProvider(
        domain=os.getenv("AGENT_RESEARCH_SANDBOX_DOMAIN", "127.0.0.1:8080"),
        protocol=os.getenv("AGENT_RESEARCH_SANDBOX_PROTOCOL", "http"),
        api_key=os.getenv("AGENT_RESEARCH_SANDBOX_API_KEY") or None,
    )
    backend = await provider.create(
        image_ref=os.getenv("AGENT_RESEARCH_SANDBOX_IMAGE", _IMAGE),
        ttl_seconds=60,
        cpu=1,
        memory_mib=2048,
        network_enabled=False,
        metadata={"smoke": "phase6-slice6-ttl"},
    )
    try:
        expired = False
        for _ in range(18):
            await asyncio.sleep(5)
            try:
                connected = await provider.connect(backend.id)
            except SandboxApiException as exc:
                assert exc.status_code == 404
                expired = True
                break
            else:
                connected.close()
        assert expired, "OpenSandbox Server 未在 90 秒观察窗内回收 60 秒 TTL Sandbox"
    finally:
        backend.close()
        await provider.destroy(backend.id)


@pytest.mark.skipif(not _RUN, reason="需要显式启用本地 OpenSandbox 安全 Smoke")
async def test_real_sandbox_command_timeout_interrupts_without_poisoning_backend() -> None:
    provider = OpenSandboxProvider(
        domain=os.getenv("AGENT_RESEARCH_SANDBOX_DOMAIN", "127.0.0.1:8080"),
        protocol=os.getenv("AGENT_RESEARCH_SANDBOX_PROTOCOL", "http"),
        api_key=os.getenv("AGENT_RESEARCH_SANDBOX_API_KEY") or None,
    )
    backend = await provider.create(
        image_ref=os.getenv("AGENT_RESEARCH_SANDBOX_IMAGE", _IMAGE),
        ttl_seconds=60,
        cpu=1,
        memory_mib=2048,
        network_enabled=False,
        metadata={"smoke": "phase6-slice6-timeout"},
    )
    try:
        started_at = time.monotonic()
        timed_out = backend.execute("sleep 5", timeout=1)
        elapsed = time.monotonic() - started_at
        assert timed_out.exit_code in {124, 137}
        assert elapsed < 4
        assert _assert_ok(backend, "printf ready") == "ready"
    finally:
        backend.close()
        await provider.destroy(backend.id)
