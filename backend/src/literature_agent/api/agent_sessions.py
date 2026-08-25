"""Project-scoped AgentSession/AgentTurn 最小 API。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from literature_agent.api.dependencies import ActorDep, CorrelationIdDep
from literature_agent.application.agent_session_service import AgentSessionService
from literature_agent.domain.exceptions import (
    AgentReviewOutputNotFoundError,
    AgentSessionBusyError,
    AgentSessionNotFoundError,
    AgentTurnNotFoundError,
    IdempotencyConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ProjectNotIndexedError,
)
from literature_agent.infrastructure.persistence.agent_repository import SqlalchemyAgentRepository
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import SqlalchemyEventRepository
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import SqlalchemyOutboxRepository
from literature_agent.infrastructure.persistence.paper_repository import SqlalchemyPaperRepository
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import SqlalchemyReviewRepository
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository

router = APIRouter(prefix="/api/v1", tags=["agent-sessions"])


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)


class SessionResponse(BaseModel):
    session_id: str
    project_id: str
    title: str | None
    status: str
    active_turn_run_id: str | None
    created_at: datetime
    last_activity_at: datetime


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16_000)
    review_output_id: str = Field(min_length=1, max_length=36)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content 不能为空白")
        return value


class MessageResponse(BaseModel):
    message_id: str
    session_id: str
    sequence: int
    role: str
    content: str
    turn_run_id: str
    created_at: datetime


class PostMessageResponse(BaseModel):
    user_message_id: str
    run_id: str
    status: str


class CandidateResponse(BaseModel):
    candidate_id: str
    name: str
    media_type: str
    content_hash: str
    size_bytes: int
    status: str


class TurnResponse(BaseModel):
    run_id: str
    session_id: str
    project_id: str
    status: str
    user_message_id: str
    context_snapshot_id: str
    policy_snapshot_id: str
    review_output_id: str
    project_index_refs: list[dict[str, str]]
    candidates: list[CandidateResponse]


async def get_agent_session_service(request: Request) -> AgentSessionService:
    state = request.app.state.app_state
    return AgentSessionService(
        session_factory=state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        event_notifier=state.event_notifier,
    )


ServiceDep = Annotated[AgentSessionService, Depends(get_agent_session_service)]


def _session(value) -> SessionResponse:
    return SessionResponse(
        session_id=value.session_id,
        project_id=value.project_id,
        title=value.title,
        status=value.status.value,
        active_turn_run_id=value.active_turn_run_id,
        created_at=value.created_at,
        last_activity_at=value.last_activity_at,
    )


def _translate(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (
            ProjectNotFoundError,
            AgentSessionNotFoundError,
            AgentTurnNotFoundError,
            AgentReviewOutputNotFoundError,
        ),
    ):
        code = (
            "agent_session_not_found"
            if isinstance(exc, AgentSessionNotFoundError)
            else "agent_turn_not_found"
            if isinstance(exc, AgentTurnNotFoundError)
            else "review_output_not_found"
            if isinstance(exc, AgentReviewOutputNotFoundError)
            else "project_not_found"
        )
        return HTTPException(404, code)
    if isinstance(exc, AgentSessionBusyError):
        return HTTPException(409, "agent_session_busy")
    if isinstance(exc, ProjectArchivedError):
        return HTTPException(409, "project_archived")
    if isinstance(exc, ProjectNotIndexedError):
        return HTTPException(409, "project_not_indexed")
    if isinstance(exc, IdempotencyConflictError):
        return HTTPException(409, "idempotency_conflict")
    raise exc


@router.post(
    "/projects/{project_id}/agent-sessions", status_code=201, response_model=SessionResponse
)
async def create_session(
    project_id: str, body: SessionCreateRequest, actor: ActorDep, service: ServiceDep
) -> SessionResponse:
    try:
        return _session(await service.create_session(actor, project_id, title=body.title))
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/agent-sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, actor: ActorDep, service: ServiceDep) -> SessionResponse:
    try:
        return _session(await service.get_session(actor, session_id))
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/agent-sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: str, actor: ActorDep, service: ServiceDep
) -> list[MessageResponse]:
    try:
        values = await service.list_messages(actor, session_id)
        return [
            MessageResponse(
                message_id=x.message_id,
                session_id=x.session_id,
                sequence=x.sequence,
                role=x.role.value,
                content=x.content,
                turn_run_id=x.turn_run_id,
                created_at=x.created_at,
            )
            for x in values
        ]
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/agent-sessions/{session_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PostMessageResponse,
)
async def post_message(
    session_id: str,
    body: MessageCreateRequest,
    actor: ActorDep,
    correlation_id: CorrelationIdDep,
    service: ServiceDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PostMessageResponse:
    if idempotency_key is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "idempotency_key_required")
    if not idempotency_key.strip() or len(idempotency_key) > 255:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "idempotency_key_invalid")
    try:
        value = await service.post_message(
            actor,
            session_id,
            content=body.content,
            review_output_id=body.review_output_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        return PostMessageResponse(
            user_message_id=value.user_message_id, run_id=value.run_id, status=value.status
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/agent-turn-runs/{run_id}", response_model=TurnResponse)
async def get_turn(run_id: str, actor: ActorDep, service: ServiceDep) -> TurnResponse:
    try:
        value = await service.get_turn(actor, run_id)
        return TurnResponse(
            run_id=value.run.run_id,
            session_id=value.turn.session_id,
            project_id=value.run.project_id,
            status=value.run.status.value,
            user_message_id=value.turn.user_message_id,
            context_snapshot_id=value.turn.context_snapshot_id,
            policy_snapshot_id=value.turn.policy_snapshot_id,
            review_output_id=value.context_snapshot.review_output_id or "",
            project_index_refs=[
                {
                    "paper_id": r.paper_id,
                    "paper_version_id": r.paper_version_id,
                    "chunk_set_id": r.chunk_set_id,
                }
                for r in value.context_snapshot.project_index_refs
            ],
            candidates=[
                CandidateResponse(
                    candidate_id=x.candidate_id,
                    name=x.name,
                    media_type=x.media_type,
                    content_hash=x.content_hash,
                    size_bytes=x.size_bytes,
                    status=x.status.value,
                )
                for x in value.candidates
            ],
        )
    except Exception as exc:
        raise _translate(exc) from exc
