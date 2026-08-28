"""受控平台 WebSocket↔OpenSandbox websockify 画面通道。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from websockets.asyncio.client import (
    ClientConnection,
)
from websockets.asyncio.client import (
    connect as websocket_connect,
)
from websockets.typing import Subprotocol

from literature_agent.application.browser_control_service import BrowserViewGrant
from literature_agent.infrastructure.agent.opensandbox_backend import OpenSandboxProvider
from literature_agent.infrastructure.persistence.models import (
    AgentBrowserControlLeaseORM,
    AgentSandboxLeaseORM,
)

VNC_FRAME_MAX_BYTES = 1024 * 1024
VNC_TRANSFER_MAX_BYTES = 64 * 1024 * 1024
VNC_IDLE_TIMEOUT_SECONDS = 60.0
VNC_VALIDATION_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True, repr=False)
class BrowserWebSocketTarget:
    url: str
    headers: dict[str, str]


class OpenSandboxBrowserTargetResolver:
    """先在短事务读取精确 fence，再在事务外解析 Provider endpoint。"""

    def __init__(self, session_factory: Any, provider: OpenSandboxProvider) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def resolve(self, grant: BrowserViewGrant) -> BrowserWebSocketTarget:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentSandboxLeaseORM).where(
                        AgentSandboxLeaseORM.session_id == grant.session_id,
                        AgentSandboxLeaseORM.owner_id == grant.owner_id,
                        AgentSandboxLeaseORM.project_id == grant.project_id,
                        AgentSandboxLeaseORM.generation == grant.sandbox_generation,
                        AgentSandboxLeaseORM.fencing_token
                        == grant.sandbox_fencing_token,
                        AgentSandboxLeaseORM.status == "active",
                    )
                )
            ).scalar_one_or_none()
            control = (
                await session.execute(
                    select(AgentBrowserControlLeaseORM.control_id).where(
                        AgentBrowserControlLeaseORM.control_id == grant.control_id,
                        AgentBrowserControlLeaseORM.status == "active",
                        AgentBrowserControlLeaseORM.viewer_connection_id
                        == grant.connection_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None or control is None:
                raise PermissionError("Browser control fence 已失效")
            sandbox_id = row.sandbox_id
        target = await self._provider.get_browser_websocket_target(sandbox_id)
        return BrowserWebSocketTarget(target.url, target.headers)


async def connect_browser_upstream(
    target: BrowserWebSocketTarget,
    *,
    frame_max_bytes: int = VNC_FRAME_MAX_BYTES,
) -> ClientConnection:
    """连接 OpenSandbox server proxy；禁用环境代理并固定 binary 子协议。"""
    return await websocket_connect(
        target.url,
        additional_headers=target.headers,
        subprotocols=[Subprotocol("binary")],
        compression=None,
        proxy=None,
        open_timeout=10.0,
        close_timeout=5.0,
        ping_interval=20.0,
        ping_timeout=20.0,
        max_size=frame_max_bytes,
        max_queue=4,
    )


async def bridge_vnc_websocket(
    websocket: Any,
    upstream: ClientConnection,
    *,
    is_current: Callable[[], Awaitable[bool]],
    total_timeout_seconds: float,
    frame_max_bytes: int = VNC_FRAME_MAX_BYTES,
    transfer_max_bytes: int = VNC_TRANSFER_MAX_BYTES,
    idle_timeout_seconds: float = VNC_IDLE_TIMEOUT_SECONDS,
    validation_interval_seconds: float = VNC_VALIDATION_INTERVAL_SECONDS,
) -> None:
    """有界双向转发；不解析、不记录 VNC 数据或用户输入。"""
    if min(
        total_timeout_seconds,
        frame_max_bytes,
        transfer_max_bytes,
        idle_timeout_seconds,
        validation_interval_seconds,
    ) <= 0:
        raise ValueError("Browser bridge 边界必须为正数")

    async def upstream_to_platform() -> None:
        transferred = 0
        while True:
            data = await asyncio.wait_for(
                upstream.recv(),
                timeout=idle_timeout_seconds,
            )
            if not isinstance(data, bytes):
                raise ValueError("Browser 下行只接受二进制帧")
            if len(data) > frame_max_bytes:
                raise ValueError("Browser 下行帧超过大小限制")
            transferred += len(data)
            if transferred > transfer_max_bytes:
                raise ValueError("Browser 下行超过总量限制")
            await websocket.send_bytes(data)

    async def platform_to_upstream() -> None:
        transferred = 0
        while True:
            message = await asyncio.wait_for(
                websocket.receive(), timeout=idle_timeout_seconds
            )
            if message.get("type") == "websocket.disconnect":
                return
            data = message.get("bytes")
            if not isinstance(data, bytes):
                raise ValueError("Browser bridge 只接受二进制帧")
            if len(data) > frame_max_bytes:
                raise ValueError("Browser 上行帧超过大小限制")
            transferred += len(data)
            if transferred > transfer_max_bytes:
                raise ValueError("Browser 上行超过总量限制")
            await upstream.send(data)

    async def fence_guard() -> None:
        while True:
            await asyncio.sleep(validation_interval_seconds)
            if not await is_current():
                raise PermissionError("Browser control 已失效")

    tasks = {
        asyncio.create_task(upstream_to_platform()),
        asyncio.create_task(platform_to_upstream()),
        asyncio.create_task(fence_guard()),
    }
    try:
        done, pending = await asyncio.wait_for(
            asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED),
            timeout=total_timeout_seconds,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await upstream.close()


def remaining_control_seconds(grant: BrowserViewGrant) -> float:
    """总连接时长永不超过业务 Lease。"""
    return max(0.0, (grant.expires_at - datetime.now(UTC)).total_seconds())
