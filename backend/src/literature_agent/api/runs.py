"""Run 相关的 HTTP 路由。"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.ports.event_notifier import EventNotifier
from literature_agent.application.run_service import RunService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.event import Event
from literature_agent.domain.exceptions import (
    InvalidRunTransitionError,
    RunNotFoundError,
)
from literature_agent.domain.run import Run, RunStatus
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class RunCreateRequest(BaseModel):
    """创建 Run 的请求体。

    注意：本端点主要用于测试和内部场景，生产环境中 Run 通常由上传、
    Worker 或其他业务操作创建。
    """

    project_id: str = Field(..., min_length=1)
    run_type: str = Field(..., min_length=1, max_length=50)
    input_payload: dict = Field(default_factory=dict)


class RunResponse(BaseModel):
    """Run 的响应模型。"""

    run_id: str
    project_id: str
    owner_id: str
    run_type: str
    status: str
    input_payload: dict
    result_payload: dict
    event_sequence: int
    created_at: datetime
    updated_at: datetime


class EventResponse(BaseModel):
    """Event 的响应模型。"""

    event_id: str
    event_version: str
    event_type: str
    run_id: str
    sequence: int
    occurred_at: datetime
    actor_type: str
    correlation_id: str
    payload: dict


async def get_run_service(request: Request) -> RunService:
    """从应用状态构建 RunService。"""
    app_state = request.app.state.app_state
    return RunService(
        session_factory=app_state.session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        event_notifier=app_state.event_notifier,
    )


RunServiceDep = Annotated[RunService, Depends(get_run_service)]


def _run_to_response(run: Run) -> RunResponse:
    """将 Run 领域实体转换为响应模型。"""
    data = asdict(run)
    data["status"] = run.status.value
    return RunResponse(**data)


def _event_to_response(event: Event) -> EventResponse:
    """将 Event 领域实体转换为响应模型。"""
    return EventResponse(**asdict(event))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RunResponse)
async def create_run(
    body: RunCreateRequest,
    actor: ActorDep,
    service: RunServiceDep,
    correlation_id: Annotated[str, Depends(lambda: "api-create-run")],
) -> RunResponse:
    """为当前 actor 创建一个测试/内部 Run。"""
    run = await service.create_run(
        actor,
        body.project_id,
        body.run_type,
        body.input_payload,
        correlation_id,
    )
    return _run_to_response(run)


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    actor: ActorDep,
    service: RunServiceDep,
) -> RunResponse:
    """获取当前 actor 可见的单个 Run。"""
    try:
        run = await service.get_run(actor, run_id)
    except RunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 不存在",
        ) from None
    return _run_to_response(run)


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(
    run_id: str,
    actor: ActorDep,
    service: RunServiceDep,
    correlation_id: Annotated[str, Depends(lambda: "api-cancel-run")],
) -> dict:
    """请求取消当前 actor 的 Run。"""
    try:
        await service.cancel_run(actor, run_id, correlation_id)
    except RunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 不存在",
        ) from None
    except InvalidRunTransitionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run 当前状态无法取消",
        ) from None
    return {"status": "cancel_requested"}


@router.get("/{run_id}/events", response_model=list[EventResponse])
async def list_events(
    run_id: str,
    actor: ActorDep,
    service: RunServiceDep,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[EventResponse]:
    """获取当前 actor 可见 Run 的事件列表，支持 sequence 游标分页。"""
    try:
        events = await service.list_events(actor, run_id, after_sequence, limit)
    except RunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 不存在",
        ) from None
    return [_event_to_response(event) for event in events]


# SSE 流参数：轮询兜底间隔、心跳注释间隔、单批拉取上限
_SSE_POLL_INTERVAL_SECONDS = 1.0
_SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0
_SSE_BATCH_LIMIT = 500

_TERMINAL_STATUSES = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


def _format_sse(event: Event) -> str:
    """把 Event 序列化为一条 SSE 帧；id 使用 sequence 字符串（重放游标）。"""
    data = json.dumps(
        _event_to_response(event).model_dump(mode="json"),
        ensure_ascii=False,
    )
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


async def _listen_notifications(
    notifier: EventNotifier,
    run_id: str,
    notified: asyncio.Event,
) -> None:
    """后台订阅任务：收到通知就唤醒流式循环；失败只记日志。"""
    try:
        async for _ in notifier.subscribe(run_id):
            notified.set()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("事件订阅失败: run_id=%s", run_id, exc_info=True)


async def _event_stream(
    service: RunService,
    notifier: EventNotifier,
    actor: ActorContext,
    run_id: str,
    after_sequence: int,
) -> AsyncIterator[str]:
    """SSE 事件流：先重放历史，再实时跟随，终态收束后关闭。

    事件永远从 PostgreSQL 读取；Pub/Sub 通知只用于降延迟，
    丢失时由 1s 轮询兜底收敛。连接不持有数据库事务，
    每轮读取都是独立短事务。
    """
    last = after_sequence
    notified = asyncio.Event()
    listener = asyncio.create_task(_listen_notifications(notifier, run_id, notified))
    try:
        last_emit_at = time.monotonic()
        while True:
            # 先读状态再读事件：终态与终态事件同事务提交，
            # 该顺序保证"终态 + 无更多事件"的收束判断不重不漏
            run = await service.get_run(actor, run_id)
            events = await service.list_events(
                actor, run_id, after_sequence=last, limit=_SSE_BATCH_LIMIT
            )
            for event in events:
                yield _format_sse(event)
                last = event.sequence
                last_emit_at = time.monotonic()
            drained = len(events) < _SSE_BATCH_LIMIT
            if run.status in _TERMINAL_STATUSES and drained:
                break
            now = time.monotonic()
            if now - last_emit_at >= _SSE_HEARTBEAT_INTERVAL_SECONDS:
                yield ": heartbeat\n\n"
                last_emit_at = now
            notified.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(notified.wait(), timeout=_SSE_POLL_INTERVAL_SECONDS)
    finally:
        listener.cancel()
        with suppress(asyncio.CancelledError):
            await listener


@router.get("/{run_id}/events/stream")
async def stream_events(
    run_id: str,
    actor: ActorDep,
    request: Request,
    service: RunServiceDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """以 SSE 流式跟随 Run 的事件：先重放历史，再实时推送，终态后关闭。

    ``Last-Event-ID`` 携带已收到的最大 sequence，断线重连后
    从其后继续重放，不重不漏。
    """
    notifier: EventNotifier = request.app.state.app_state.event_notifier
    try:
        await service.get_run(actor, run_id)
    except RunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 不存在",
        ) from None
    after_sequence = 0
    if last_event_id:
        try:
            after_sequence = int(last_event_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Last-Event-ID 必须是事件 sequence 整数",
            ) from None
        if after_sequence < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Last-Event-ID 必须是非负整数",
            )
    return StreamingResponse(
        _event_stream(service, notifier, actor, run_id, after_sequence),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
