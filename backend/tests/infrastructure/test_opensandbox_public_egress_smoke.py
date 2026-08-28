"""显式 opt-in 的 Slice 7 public-egress 真实证据；不调用模型。"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import struct
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from literature_agent.domain.agent_network import RESEARCH_PUBLIC_EGRESS_PROFILE
from literature_agent.infrastructure.agent.opensandbox_backend import OpenSandboxProvider
from literature_agent.infrastructure.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_OPENSANDBOX_PUBLIC_EGRESS_TESTS") != "1",
    reason="需要显式启用 Slice 7 OpenSandbox public-egress Smoke",
)


def _assert_ok(backend, command: str, *, timeout: int = 20) -> str:
    result = backend.execute(command, timeout=timeout)
    assert result.exit_code == 0, result.output
    return result.output


async def _call_mcp(
    *, endpoint: str, headers: dict[str, str], service: str, tool: str, arguments: dict
):
    parts = urlsplit(endpoint)
    url = urlunsplit(
        (parts.scheme, parts.netloc, f"{parts.path.rstrip('/')}/mcp/", parts.query, "")
    )
    client = MultiServerMCPClient(
        {service: {"transport": "streamable_http", "url": url, "headers": headers}}
    )
    async with client.session(service) as session:
        return await session.call_tool(tool, arguments)


async def test_public_egress_covers_execute_browser_and_mcp_but_denies_private_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings()
    provider = OpenSandboxProvider(
        domain=os.getenv("AGENT_RESEARCH_SANDBOX_DOMAIN", defaults.research_sandbox_domain),
        protocol=os.getenv(
            "AGENT_RESEARCH_SANDBOX_PROTOCOL", defaults.research_sandbox_protocol
        ),
        api_key=os.getenv("AGENT_RESEARCH_SANDBOX_API_KEY") or None,
    )
    backend = await provider.create(
        image_ref=os.getenv("AGENT_RESEARCH_SANDBOX_IMAGE", defaults.research_sandbox_image),
        ttl_seconds=300,
        cpu=1,
        memory_mib=2048,
        network_enabled=True,
        network_profile_id=RESEARCH_PUBLIC_EGRESS_PROFILE.profile_id,
        network_profile_version=RESEARCH_PUBLIC_EGRESS_PROFILE.version,
        network_profile_hash=RESEARCH_PUBLIC_EGRESS_PROFILE.profile_hash,
        metadata={"smoke": "phase6-slice7-public-egress"},
    )
    try:
        assert _assert_ok(backend, "command -v wget").strip() == "/usr/bin/wget"
        _assert_ok(
            backend,
            "python3 -m http.server 8765 --bind 127.0.0.1 >/tmp/http.log 2>&1 & "
            "pid=$!; sleep 1; /usr/bin/wget -q --timeout=3 --tries=1 "
            "-O /dev/null http://127.0.0.1:8765/; fetch_code=$?; "
            "kill $pid; wait $pid 2>/dev/null || true; exit \"$fetch_code\"",
        )
        _assert_ok(
            backend,
            "/usr/bin/wget -q --spider --timeout=20 --tries=1 https://arxiv.org/",
        )
        pdf_probe = _assert_ok(
            backend,
            "python3 -c \"import hashlib, urllib.request; "
            "request = urllib.request.Request("
            "'https://arxiv.org/pdf/1706.03762', "
            "headers={'Range': 'bytes=0-65535', 'Accept-Encoding': 'identity'}); "
            "response = urllib.request.urlopen(request, timeout=20); "
            "status = response.status; "
            "content_type = response.headers.get('Content-Type', '').lower(); "
            "prefix = response.read(65536); response.close(); "
            "assert status in (200, 206), status; "
            "assert content_type.startswith('application/pdf'), content_type; "
            "assert prefix and prefix.startswith(b'%PDF'), prefix[:16]; "
            "print(status, len(prefix), hashlib.sha256(prefix).hexdigest())\"",
            timeout=30,
        ).split()
        assert pdf_probe[0] in {"200", "206"}
        assert 0 < int(pdf_probe[1]) <= 65536
        assert re.fullmatch(r"[0-9a-f]{64}", pdf_probe[2])
        _assert_ok(
            backend,
            "python3 -c \"import urllib.request; "
            "urllib.request.urlopen('https://example.com', timeout=10).read(1)\"",
        )
        _assert_ok(
            backend,
            "node -e \"fetch('https://example.com').then(r=>{if(!r.ok)process.exit(9)})\"",
        )
        _assert_ok(
            backend,
            "chromium --headless --no-sandbox --disable-gpu --dump-dom "
            "https://example.com/ | grep -q 'Example Domain'",
        )

        gateway = socket.inet_ntoa(
            struct.pack(
                "<L",
                int(_assert_ok(backend, "awk 'NR==2 {print $3}' /proc/net/route").strip(), 16),
            )
        )
        for target in (
            "http://169.254.169.254/latest/meta-data/",
            f"http://{gateway}:8080/",
            "http://10.0.0.1/",
        ):
            blocked = backend.execute(
                "output=$(/usr/bin/wget --server-response --spider "
                f"--timeout=3 --tries=1 {target!r} 2>&1); code=$?; "
                "printf '%s\\n' \"$output\"; "
                "printf 'WGET_NETWORK_FAILURE exit=%s\\n' \"$code\"; exit \"$code\"",
                timeout=5,
            )
            assert blocked.exit_code != 0
            assert blocked.exit_code != 127
            assert "WGET_NETWORK_FAILURE exit=" in blocked.output
            failure_output = blocked.output.lower()
            assert any(
                marker in failure_output
                for marker in (
                    "connection timed out",
                    "connection refused",
                    "connection reset",
                    "network is unreachable",
                    "no route to host",
                    "operation not permitted",
                    "permission denied",
                )
            ), blocked.output

        for service, port, tool_name, arguments in (
            ("playwright", 8931, "browser_navigate", {"url": "https://example.com"}),
            ("arxiv-search", 8932, "search_papers", {"query": "agent", "max_results": 1}),
        ):
            backend.prepare_mcp_service(service)
            endpoint, headers, authority = backend.get_mcp_endpoint(port)
            backend.configure_mcp_service(service, allowed_host=authority)
            endpoint_host = urlsplit(endpoint).hostname
            assert endpoint_host is not None
            monkeypatch.setenv("NO_PROXY", f"127.0.0.1,localhost,{endpoint_host}")
            monkeypatch.setenv("no_proxy", f"127.0.0.1,localhost,{endpoint_host}")
            for name in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
                monkeypatch.delenv(name, raising=False)
            result = await asyncio.wait_for(
                _call_mcp(
                    endpoint=endpoint,
                    headers=headers,
                    service=service,
                    tool=tool_name,
                    arguments=arguments,
                ),
                timeout=30,
            )
            assert result.isError is not True
    finally:
        with suppress(Exception):
            backend.close()
        await provider.destroy(backend.id)
