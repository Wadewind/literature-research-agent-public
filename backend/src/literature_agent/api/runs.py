"""Run 相关的 HTTP 路由。"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.run_service import RunService
from literature_agent.domain.event import Event
from literature_agent.domain.exceptions import (
    InvalidRunTransitionError,
    RunNotFoundError,
)
from literature_agent.domain.run import Run
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)

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


def get_run_service(request: Request) -> RunService:
    """从应用状态构建 RunService。"""
    app_state = request.app.state.app_state
    return RunService(
        session_factory=app_state.session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
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
) -> list[EventResponse]:
    """获取当前 actor 可见 Run 的事件列表。"""
    try:
        events = await service.list_events(actor, run_id)
    except RunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 不存在",
        ) from None
    return [_event_to_response(event) for event in events]
