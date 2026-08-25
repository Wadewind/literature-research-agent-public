"""固定文献综述 Workflow 的 Project-scoped HTTP API。"""

import json
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from literature_agent.api.dependencies import ActorDep, CorrelationIdDep
from literature_agent.application.review_outline_service import HumanOutlineInputService
from literature_agent.application.review_query_service import ReviewOutputType, ReviewQueryService
from literature_agent.application.review_workflow_service import ReviewWorkflowService
from literature_agent.application.run_service import RunService
from literature_agent.domain.exceptions import (
    HumanInputConflictError,
    IdempotencyConflictError,
    InvalidRunTransitionError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ReviewOutlineScopeError,
    RunNotFoundError,
)
from literature_agent.infrastructure.persistence.event_repository import SqlalchemyEventRepository
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import SqlalchemyOutboxRepository
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import SqlalchemyReviewRepository
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository

router = APIRouter(prefix="/api/v1/projects/{project_id}/reviews", tags=["reviews"])


class ReviewCreateRequest(BaseModel):
    research_question: str = Field(min_length=1, max_length=4_000)


class OutlineInputRequest(BaseModel):
    request_id: str = Field(min_length=1)
    request_version: int = Field(ge=1)
    outline_output_id: str = Field(min_length=1)
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


async def get_review_query_service(request: Request) -> ReviewQueryService:
    state = request.app.state.app_state
    return ReviewQueryService(
        session_factory=state.session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        storage=state.storage,
    )


async def get_review_workflow_service(request: Request) -> ReviewWorkflowService:
    state = request.app.state.app_state
    return ReviewWorkflowService(
        session_factory=state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        event_notifier=state.event_notifier,
    )


async def get_outline_input_service(request: Request) -> HumanOutlineInputService:
    state = request.app.state.app_state
    return HumanOutlineInputService(
        session_factory=state.session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        event_notifier=state.event_notifier,
    )


async def get_review_run_service(request: Request) -> RunService:
    state = request.app.state.app_state
    return RunService(
        session_factory=state.session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        event_notifier=state.event_notifier,
    )


ReviewQueryDep = Annotated[ReviewQueryService, Depends(get_review_query_service)]
ReviewWorkflowDep = Annotated[ReviewWorkflowService, Depends(get_review_workflow_service)]
OutlineInputDep = Annotated[HumanOutlineInputService, Depends(get_outline_input_service)]
ReviewRunDep = Annotated[RunService, Depends(get_review_run_service)]


@router.get("")
async def list_reviews(
    project_id: str, actor: ActorDep, query: ReviewQueryDep
) -> list[dict]:
    return [
        {
            "run_id": run.run_id,
            "status": run.status.value,
            "research_question": review.research_question,
            "current_stage": review.current_stage.value,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }
        for run, review in await query.list_reviews(actor, project_id)
    ]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_review(
    project_id: str,
    body: ReviewCreateRequest,
    actor: ActorDep,
    service: ReviewWorkflowDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    if idempotency_key is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 Idempotency-Key 请求头")
    try:
        result = await service.create_review_run(
            actor, project_id, body.research_question, idempotency_key, correlation_id
        )
    except (ProjectNotFoundError, RunNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project 不存在") from None
    except ProjectArchivedError:
        raise HTTPException(status.HTTP_409_CONFLICT, "project_archived") from None
    except IdempotencyConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_conflict") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return Response(
        content=json.dumps(asdict(result), ensure_ascii=False),
        status_code=status.HTTP_200_OK if result.reused else status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


@router.get("/{run_id}")
async def review_detail(
    project_id: str, run_id: str, actor: ActorDep, query: ReviewQueryDep
) -> dict:
    try:
        run, review, steps, input_request = await query.detail(actor, project_id, run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None
    return {
        "run": _value(asdict(run)),
        "review": _value(asdict(review)),
        "steps": [_value(asdict(item)) for item in steps],
        "open_human_input_request": _value(asdict(input_request)) if input_request else None,
    }


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_review(
    project_id: str,
    run_id: str,
    actor: ActorDep,
    query: ReviewQueryDep,
    service: ReviewRunDep,
    correlation_id: CorrelationIdDep,
) -> dict:
    try:
        await query.detail(actor, project_id, run_id)
        await service.cancel_run(actor, run_id, correlation_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None
    except InvalidRunTransitionError:
        raise HTTPException(status.HTTP_409_CONFLICT, "review_cannot_cancel") from None
    return {"status": "cancel_requested"}


@router.get("/{run_id}/sources")
async def list_sources(
    project_id: str, run_id: str, actor: ActorDep, query: ReviewQueryDep
) -> list[dict]:
    try:
        return [_value(asdict(item)) for item in await query.sources(actor, project_id, run_id)]
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None


async def _output(project_id, run_id, actor, query, kind, output_key):
    try:
        output = await query.output(actor, project_id, run_id, kind, output_key)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None
    if output is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Output 尚未生成")
    return _value(asdict(output))


@router.get("/{run_id}/evidence-matrix")
async def evidence_matrix(
    project_id: str, run_id: str, actor: ActorDep, query: ReviewQueryDep
) -> dict:
    return await _output(
        project_id,
        run_id,
        actor,
        query,
        ReviewOutputType.EVIDENCE_MATRIX,
        "evidence-matrix",
    )


@router.get("/{run_id}/outline")
async def outline(project_id: str, run_id: str, actor: ActorDep, query: ReviewQueryDep) -> dict:
    return await _output(project_id, run_id, actor, query, ReviewOutputType.OUTLINE, "outline")


@router.get("/{run_id}/sections")
async def sections(
    project_id: str, run_id: str, actor: ActorDep, query: ReviewQueryDep
) -> list[dict]:
    try:
        return [_value(asdict(item)) for item in await query.sections(actor, project_id, run_id)]
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None


@router.post("/{run_id}/outline-input")
async def submit_outline_input(
    project_id: str,
    run_id: str,
    body: OutlineInputRequest,
    actor: ActorDep,
    service: OutlineInputDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    if idempotency_key is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 Idempotency-Key 请求头")
    try:
        result = await service.submit(
            run_id=run_id,
            project_id=project_id,
            owner_id=actor.owner_id,
            request_id=body.request_id,
            request_version=body.request_version,
            outline_output_id=body.outline_output_id,
            action=body.action,
            payload=body.payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None
    except (
        HumanInputConflictError,
        ReviewOutlineScopeError,
        IdempotencyConflictError,
        ValueError,
    ) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return _value(asdict(result))


@router.get("/{run_id}/artifacts")
async def list_artifacts(
    project_id: str, run_id: str, actor: ActorDep, query: ReviewQueryDep
) -> list[dict]:
    try:
        return [_value(asdict(item)) for item in await query.artifacts(actor, project_id, run_id)]
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None


@router.get("/{run_id}/artifacts/{artifact_id}/content")
async def artifact_content(
    project_id: str,
    run_id: str,
    artifact_id: str,
    actor: ActorDep,
    query: ReviewQueryDep,
) -> Response:
    try:
        artifact, content = await query.artifact_content(actor, project_id, run_id, artifact_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact 不存在") from None
    return Response(
        content=content,
        media_type=artifact.media_type,
        headers={"ETag": f'"{artifact.content_hash}"'},
    )


@router.get("/{run_id}/events")
async def review_events(
    project_id: str,
    run_id: str,
    actor: ActorDep,
    query: ReviewQueryDep,
    service: ReviewRunDep,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    try:
        await query.detail(actor, project_id, run_id)
        return [
            _value(asdict(item))
            for item in await service.list_events(actor, run_id, after_sequence, limit)
        ]
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review 不存在") from None


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_value(item) for item in value]
    return value.value if hasattr(value, "value") else value
