"""真实 OpenSandbox MCP 回路冒烟测试（默认跳过）。

显式启用：
``AGENT_RUN_OPENSANDBOX_MCP_TESTS=1 uv run pytest
tests/infrastructure/test_opensandbox_mcp_smoke.py -q``。

测试只访问 Sandbox 内合成页面；Sandbox 网络仍为 default-deny，不访问公网 arXiv。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp import ClientSession
from mcp.types import Tool

from literature_agent.domain.mcp_configuration import canonical_json_hash
from literature_agent.infrastructure.agent.mcp_catalog import PLATFORM_MCP_CATALOG
from literature_agent.infrastructure.agent.opensandbox_backend import OpenSandboxProvider
from literature_agent.infrastructure.agent.sandbox_mcp import McpSandboxServerRecipe
from literature_agent.infrastructure.config import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_OPENSANDBOX_MCP_TESTS") != "1",
    reason=("真实 OpenSandbox MCP 冒烟测试需显式启用（AGENT_RUN_OPENSANDBOX_MCP_TESTS=1）"),
)

_SYNTHETIC_SERVER = b"""\
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path('/workspace/synthetic')
root.mkdir(parents=True, exist_ok=True)
(root / 'index.html').write_text(
    '<a id="download" href="/paper.txt" download>download paper</a>',
    encoding='utf-8',
)
(root / 'paper.txt').write_text('synthetic paper payload', encoding='utf-8')

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(root), **kwargs)

ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
"""


@asynccontextmanager
async def _mcp_session(
    endpoint: str,
    headers: dict[str, str],
    mcp_path: str,
) -> AsyncIterator[ClientSession]:
    parts = urlsplit(endpoint)
    url = urlunsplit(
        (parts.scheme, parts.netloc, f"{parts.path.rstrip('/')}{mcp_path}", parts.query, "")
    )
    client = MultiServerMCPClient(
        {
            "smoke": {
                "transport": "streamable_http",
                "url": url,
                "headers": headers,
            }
        }
    )
    async with client.session("smoke") as session:
        yield session


async def _all_tools(session: ClientSession) -> tuple[Tool, ...]:
    tools: list[Tool] = []
    cursor: str | None = None
    while True:
        page = (
            await session.list_tools()
            if cursor is None
            else await session.list_tools(cursor=cursor)
        )
        tools.extend(page.tools)
        cursor = page.nextCursor
        if cursor is None:
            return tuple(tools)


async def test_real_opensandbox_browser_download_and_catalog_projection() -> None:
    """验证代理 Host/header、同一 Chromium、下载及两套真实 Schema。"""
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
        metadata={"test": "phase5-slice7.3"},
    )
    sandbox_id = backend.id
    try:
        uploaded = await asyncio.to_thread(
            backend.upload_files,
            [("/workspace/serve_synthetic.py", _SYNTHETIC_SERVER)],
        )
        assert uploaded[0].error is None
        started = await asyncio.to_thread(
            backend.execute,
            "python /workspace/serve_synthetic.py >/tmp/synthetic-http.log 2>&1 &",
        )
        assert started.exit_code == 0

        catalog = {entry.catalog_id: entry for entry in PLATFORM_MCP_CATALOG.entries}
        for service_name, port in (("playwright", 8931), ("arxiv-search", 8932)):
            await asyncio.to_thread(backend.prepare_mcp_service, service_name)
            endpoint, headers, authority = await asyncio.to_thread(
                backend.get_mcp_endpoint,
                port,
            )
            await asyncio.to_thread(
                backend.configure_mcp_service,
                service_name,
                allowed_host=authority,
            )
            recipe = McpSandboxServerRecipe(
                catalog_id=service_name,
                version=catalog[service_name].version,
                service_name=service_name,
                port=port,
            )
            async with _mcp_session(endpoint, headers, recipe.mcp_path) as session:
                tools = await _all_tools(session)
                actual = {tool.name: canonical_json_hash(tool.inputSchema) for tool in tools}
                expected = {
                    tool.name: tool.input_schema_hash for tool in catalog[service_name].tools
                }
                assert all(actual.get(name) == value for name, value in expected.items())
                if service_name == "playwright":
                    navigated = await session.call_tool(
                        "browser_navigate", {"url": "http://127.0.0.1:8765"}
                    )
                    clicked = await session.call_tool("browser_click", {"target": "a#download"})
                    assert navigated.isError is not True
                    assert clicked.isError is not True

        downloaded = await asyncio.to_thread(
            backend.download_files, ["/workspace/downloads/paper.txt"]
        )
        assert downloaded[0].error is None
        assert downloaded[0].content == b"synthetic paper payload"
    finally:
        try:
            await asyncio.to_thread(backend.close)
        finally:
            await provider.destroy(sandbox_id)
