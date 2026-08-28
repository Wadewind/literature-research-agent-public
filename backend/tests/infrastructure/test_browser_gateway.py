import asyncio

import pytest

from literature_agent.infrastructure.agent.browser_gateway import (
    BrowserWebSocketTarget,
    bridge_vnc_websocket,
    connect_browser_upstream,
)


class _WebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def receive(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Future()


class _Upstream:
    def __init__(self, messages=None) -> None:
        self.messages = list(messages or [])
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Future()

    async def close(self) -> None:
        self.closed = True

@pytest.mark.asyncio
async def test_bridge_forwards_binary_both_ways_and_closes_upstream() -> None:
    websocket = _WebSocket(
        [
            {"type": "websocket.receive", "bytes": b"client"},
            {"type": "websocket.disconnect"},
        ]
    )
    upstream = _Upstream([b"RFB 003.008\n"])

    await bridge_vnc_websocket(
        websocket,
        upstream,  # type: ignore[arg-type]
        is_current=_true,
        total_timeout_seconds=1,
        validation_interval_seconds=0.01,
    )

    assert websocket.sent == [b"RFB 003.008\n"]
    assert upstream.closed
    assert upstream.sent in ([b"client"], [])


@pytest.mark.asyncio
async def test_bridge_rejects_text_and_oversized_frames() -> None:
    for message in (
        {"type": "websocket.receive", "text": "secret"},
        {"type": "websocket.receive", "bytes": b"x" * 9},
    ):
        websocket = _WebSocket([message])
        upstream = _Upstream()
        with pytest.raises(ValueError):
            await bridge_vnc_websocket(
                websocket,
                upstream,  # type: ignore[arg-type]
                is_current=_true,
                total_timeout_seconds=1,
                frame_max_bytes=8,
                validation_interval_seconds=0.01,
            )

    websocket = _WebSocket([])
    upstream = _Upstream(["base64-is-not-accepted"])
    with pytest.raises(ValueError, match="二进制"):
        await bridge_vnc_websocket(
            websocket,
            upstream,  # type: ignore[arg-type]
            is_current=_true,
            total_timeout_seconds=1,
            validation_interval_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_bridge_stops_when_generation_guard_fails() -> None:
    websocket = _WebSocket([])
    upstream = _Upstream()
    with pytest.raises(PermissionError, match="失效"):
        await bridge_vnc_websocket(
            websocket,
            upstream,  # type: ignore[arg-type]
            is_current=_false,
            total_timeout_seconds=1,
            validation_interval_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_upstream_connection_uses_headers_binary_and_bounded_options(
    monkeypatch,
) -> None:
    calls = []
    upstream = _Upstream()

    async def connect(url, **kwargs):
        calls.append((url, kwargs))
        return upstream

    monkeypatch.setattr(
        "literature_agent.infrastructure.agent.browser_gateway.websocket_connect",
        connect,
    )
    target = BrowserWebSocketTarget(
        url="wss://sandbox.invalid/private",
        headers={"X-Sandbox-Route": "opaque"},
    )

    assert "sandbox.invalid" not in repr(target)
    assert "opaque" not in repr(target)
    assert await connect_browser_upstream(target) is upstream
    assert calls == [
        (
            "wss://sandbox.invalid/private",
            {
                "additional_headers": {"X-Sandbox-Route": "opaque"},
                "subprotocols": ["binary"],
                "compression": None,
                "proxy": None,
                "open_timeout": 10.0,
                "close_timeout": 5.0,
                "ping_interval": 20.0,
                "ping_timeout": 20.0,
                "max_size": 1024 * 1024,
                "max_queue": 4,
            },
        )
    ]


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False
