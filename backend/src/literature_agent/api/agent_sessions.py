"""Project-scoped AgentSession/AgentTurn 最小 API。"""

from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from literature_agent.api.dependencies import ActorDep, CorrelationIdDep
from literature_agent.application.agent_artifact_service import (
    AgentArtifactQueryService,
    AgentArtifactServiceError,
)
from literature_agent.application.agent_session_service import AgentSessionService
from literature_agent.application.mcp_configuration_service import (
    McpConfigurationService,
    McpProfileView,
)
from literature_agent.application.skill_configuration_service import (
    SkillConfigurationService,
    SkillProfileView,
)
from literature_agent.domain.exceptions import (
    AgentArtifactNotFoundError,
    AgentAttachmentNotFoundError,
    AgentBrowserControlBusyError,
    AgentReviewOutputNotFoundError,
    AgentSessionBusyError,
    AgentSessionNotFoundError,
    AgentTurnNotFoundError,
    IdempotencyConflictError,
    McpProfileInvalidError,
    McpProfileRevisionConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ProjectNotIndexedError,
    SkillConfigurationInvalidError,
    SkillNotFoundError,
    SkillProfileLockedError,
    SkillProfileRevisionConflictError,
    SkillVersionConflictError,
)
from literature_agent.domain.mcp_configuration import McpProfileSelection
from literature_agent.domain.skill_configuration import SkillProfileSelection, SkillSource
from literature_agent.infrastructure.agent.mcp_catalog import PLATFORM_MCP_CATALOG
from literature_agent.infrastructure.agent.skill_catalog import PLATFORM_SKILLS
from literature_agent.infrastructure.persistence.agent_attachment_repository import (
    SqlalchemyAgentAttachmentRepository,
)
from literature_agent.infrastructure.persistence.agent_repository import SqlalchemyAgentRepository
from literature_agent.infrastructure.persistence.agent_usage_repository import (
    SqlalchemyAgentUsageRepository,
)
from literature_agent.infrastructure.persistence.browser_control_repository import (
    SqlalchemyBrowserControlRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import SqlalchemyEventRepository
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.mcp_profile_repository import (
    SqlalchemyMcpProfileRepository,
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
from literature_agent.infrastructure.persistence.skill_repository import SqlalchemySkillRepository

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


class ProjectAgentContextSummaryResponse(BaseModel):
    ready_index_count: int


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16_000)
    review_output_id: str = Field(min_length=1, max_length=36)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content 不能为空白")
        return value

    @field_validator("attachment_ids")
    @classmethod
    def validate_attachment_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 36 for item in value):
            raise ValueError("attachment_ids 含非法 ID")
        if len(set(value)) != len(value):
            raise ValueError("attachment_ids 不得重复")
        return value


class CitationResponse(BaseModel):
    evidence_id: str
    paper_id: str
    version_id: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    excerpt: str


class ClaimResponse(BaseModel):
    text: str
    citations: list[CitationResponse]


class MessageResponse(BaseModel):
    message_id: str
    session_id: str
    sequence: int
    role: str
    content: str
    turn_run_id: str
    claim_set_id: str | None
    created_at: datetime
    claims: list[ClaimResponse] | None
    attachment_ids: list[str]


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


class AgentArtifactResponse(BaseModel):
    artifact_id: str
    turn_run_id: str
    name: str
    media_type: str
    content_hash: str
    size_bytes: int
    previewable: bool
    created_at: datetime


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
    attachment_refs: list[dict[str, str | int]]
    candidates: list[CandidateResponse]


class AgentTurnUsageResponse(BaseModel):
    max_model_calls: int
    max_tool_calls: int
    model_calls_reserved: int
    tool_calls_reserved: int
    wall_clock_limit_seconds: int
    tool_timeout_seconds: int
    execute_timeout_seconds: int
    max_tool_output_bytes: int
    max_repeated_tool_calls: int
    max_input_tokens_per_model_call: int
    max_output_tokens_per_model_call: int
    input_tokens: int | None
    output_tokens: int | None
    started_at: datetime | None
    deadline_at: datetime | None


class AgentToolExecutionResponse(BaseModel):
    invocation_id: str
    tool_name: str
    tool_version: str
    input_schema_hash: str
    args_hash: str
    status: str
    input_size_bytes: int
    output_size_bytes: int | None
    result_hash: str | None
    error_code: str | None
    safe_message: str | None
    duration_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None


class AgentToolExecutionsResponse(BaseModel):
    usage: AgentTurnUsageResponse
    items: list[AgentToolExecutionResponse]


class McpProfileSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_id: str = Field(min_length=1, max_length=63)
    version: str = Field(min_length=1, max_length=50)
    parameters: dict[str, str] = Field(default_factory=dict)


class McpProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    selections: list[McpProfileSelectionRequest] = Field(default_factory=list, max_length=8)


class McpProfileResponse(BaseModel):
    session_id: str
    revision: int
    config_hash: str
    selections: list[McpProfileSelectionRequest]


class McpCatalogEntryResponse(BaseModel):
    catalog_id: str
    version: str
    display_name: str
    parameters: list[dict[str, object]]
    tools: list[dict[str, str]]


class SkillCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_024)
    instructions: str = Field(min_length=1, max_length=32_000)
    required_tool_names: list[str] = Field(default_factory=list, max_length=32)


class SkillVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=1_024)
    instructions: str = Field(min_length=1, max_length=32_000)
    required_tool_names: list[str] = Field(default_factory=list, max_length=32)


class SkillResponse(BaseModel):
    skill_id: str
    source: str
    version: int
    name: str
    description: str
    instructions: str
    required_tool_names: list[str]
    content_hash: str


class SkillProfileSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SkillSource
    skill_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class SkillProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    selections: list[SkillProfileSelectionRequest] = Field(default_factory=list, max_length=8)


class SkillProfileResponse(BaseModel):
    session_id: str
    revision: int
    config_hash: str
    selections: list[SkillProfileSelectionRequest]


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
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        mcp_profile_repo_factory=SqlalchemyMcpProfileRepository,
        mcp_catalog=PLATFORM_MCP_CATALOG,
        skill_repo_factory=SqlalchemySkillRepository,
        platform_skills=PLATFORM_SKILLS,
        event_notifier=state.event_notifier,
        browser_control_repo_factory=SqlalchemyBrowserControlRepository,
        attachment_repo_factory=SqlalchemyAgentAttachmentRepository,
        agent_usage_repo_factory=SqlalchemyAgentUsageRepository,
    )


ServiceDep = Annotated[AgentSessionService, Depends(get_agent_session_service)]


async def get_agent_artifact_query_service(
    request: Request,
) -> AgentArtifactQueryService:
    state = request.app.state.app_state
    return AgentArtifactQueryService(
        session_factory=state.session_factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        storage=state.storage,
    )


AgentArtifactServiceDep = Annotated[
    AgentArtifactQueryService, Depends(get_agent_artifact_query_service)
]


async def get_mcp_configuration_service(request: Request) -> McpConfigurationService:
    state = request.app.state.app_state
    return McpConfigurationService(
        session_factory=state.session_factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        profile_repo_factory=SqlalchemyMcpProfileRepository,
        catalog=PLATFORM_MCP_CATALOG,
    )


McpServiceDep = Annotated[
    McpConfigurationService, Depends(get_mcp_configuration_service)
]


async def get_skill_configuration_service(request: Request) -> SkillConfigurationService:
    state = request.app.state.app_state
    return SkillConfigurationService(
        session_factory=state.session_factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        skill_repo_factory=SqlalchemySkillRepository,
        platform_skills=PLATFORM_SKILLS,
    )


SkillServiceDep = Annotated[
    SkillConfigurationService, Depends(get_skill_configuration_service)
]


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
            AgentAttachmentNotFoundError,
        ),
    ):
        code = (
            "agent_attachment_not_found"
            if isinstance(exc, AgentAttachmentNotFoundError)
            else "agent_session_not_found"
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
    if isinstance(exc, AgentBrowserControlBusyError):
        return HTTPException(409, "agent_browser_control_active")
    if isinstance(exc, ProjectArchivedError):
        return HTTPException(409, "project_archived")
    if isinstance(exc, ProjectNotIndexedError):
        return HTTPException(409, "project_not_indexed")
    if isinstance(exc, IdempotencyConflictError):
        return HTTPException(409, "idempotency_conflict")
    if isinstance(exc, McpProfileRevisionConflictError):
        return HTTPException(409, "mcp_profile_revision_conflict")
    if isinstance(exc, McpProfileInvalidError):
        return HTTPException(422, "mcp_profile_invalid")
    if isinstance(exc, SkillNotFoundError):
        return HTTPException(404, "skill_not_found")
    if isinstance(exc, SkillProfileLockedError):
        return HTTPException(409, "skill_profile_locked")
    if isinstance(exc, SkillProfileRevisionConflictError):
        return HTTPException(409, "skill_revision_conflict")
    if isinstance(exc, SkillVersionConflictError):
        return HTTPException(409, "skill_version_conflict")
    if isinstance(exc, SkillConfigurationInvalidError):
        return HTTPException(422, "skill_configuration_invalid")
    raise exc


def _mcp_profile(value: McpProfileView) -> McpProfileResponse:
    return McpProfileResponse(
        session_id=value.session_id,
        revision=value.revision,
        config_hash=value.config_hash,
        selections=[
            McpProfileSelectionRequest(
                catalog_id=item.catalog_id,
                version=item.version,
                parameters=dict(item.parameters),
            )
            for item in value.selections
        ],
    )


def _skill(value) -> SkillResponse:
    return SkillResponse(
        skill_id=value.skill_id,
        source=value.source.value,
        version=value.version,
        name=value.name,
        description=value.description,
        instructions=value.instructions,
        required_tool_names=list(value.required_tool_names),
        content_hash=value.content_hash,
    )


def _skill_profile(value: SkillProfileView) -> SkillProfileResponse:
    return SkillProfileResponse(
        session_id=value.session_id,
        revision=value.revision,
        config_hash=value.config_hash,
        selections=[
            SkillProfileSelectionRequest(
                source=item.source,
                skill_id=item.skill_id,
                version=item.version,
            )
            for item in value.selections
        ],
    )


@router.get("/agent-mcp-catalog", response_model=list[McpCatalogEntryResponse])
async def list_mcp_catalog(service: McpServiceDep) -> list[McpCatalogEntryResponse]:
    return [
        McpCatalogEntryResponse(
            catalog_id=entry.catalog_id,
            version=entry.version,
            display_name=entry.display_name,
            parameters=[
                {
                    "name": item.name,
                    "required": item.required,
                    "max_length": item.max_length,
                }
                for item in entry.parameters
            ],
            tools=[
                {"name": item.name, "input_schema_hash": item.input_schema_hash}
                for item in entry.tools
            ],
        )
        for entry in service.list_catalog()
    ]


@router.get(
    "/agent-sessions/{session_id}/mcp-profile", response_model=McpProfileResponse
)
async def get_mcp_profile(
    session_id: str, actor: ActorDep, service: McpServiceDep
) -> McpProfileResponse:
    try:
        return _mcp_profile(await service.get_profile(actor, session_id))
    except Exception as exc:
        raise _translate(exc) from exc


@router.put(
    "/agent-sessions/{session_id}/mcp-profile", response_model=McpProfileResponse
)
async def update_mcp_profile(
    session_id: str,
    body: McpProfileUpdateRequest,
    actor: ActorDep,
    service: McpServiceDep,
) -> McpProfileResponse:
    try:
        selections = tuple(
            McpProfileSelection(
                catalog_id=item.catalog_id,
                version=item.version,
                parameters=tuple(sorted(item.parameters.items())),
            )
            for item in body.selections
        )
        return _mcp_profile(
            await service.update_profile(
                actor,
                session_id,
                expected_revision=body.expected_revision,
                selections=selections,
            )
        )
    except ValueError as exc:
        raise HTTPException(422, "mcp_profile_invalid") from exc
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/agent-skills", response_model=list[SkillResponse])
async def list_agent_skills(
    actor: ActorDep, service: SkillServiceDep
) -> list[SkillResponse]:
    return [_skill(value) for value in await service.list_available(actor)]


@router.post("/agent-skills", status_code=201, response_model=SkillResponse)
async def create_agent_skill(
    body: SkillCreateRequest, actor: ActorDep, service: SkillServiceDep
) -> SkillResponse:
    try:
        return _skill(
            await service.create_owner_skill(
                actor,
                name=body.name,
                description=body.description,
                instructions=body.instructions,
                required_tool_names=tuple(body.required_tool_names),
            )
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/agent-skills/{skill_id}/versions", status_code=201, response_model=SkillResponse
)
async def create_agent_skill_version(
    skill_id: str,
    body: SkillVersionCreateRequest,
    actor: ActorDep,
    service: SkillServiceDep,
) -> SkillResponse:
    try:
        return _skill(
            await service.create_owner_version(
                actor,
                skill_id,
                expected_version=body.expected_version,
                description=body.description,
                instructions=body.instructions,
                required_tool_names=tuple(body.required_tool_names),
            )
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/projects/{project_id}/agent-sessions", response_model=list[SessionResponse]
)
async def list_sessions(
    project_id: str, actor: ActorDep, service: ServiceDep
) -> list[SessionResponse]:
    try:
        return [_session(value) for value in await service.list_sessions(actor, project_id)]
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/projects/{project_id}/agent-context-summary",
    response_model=ProjectAgentContextSummaryResponse,
)
async def get_project_agent_context_summary(
    project_id: str, actor: ActorDep, service: ServiceDep
) -> ProjectAgentContextSummaryResponse:
    try:
        return ProjectAgentContextSummaryResponse(
            ready_index_count=await service.get_project_ready_index_count(actor, project_id)
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/agent-sessions/{session_id}/skill-profile",
    response_model=SkillProfileResponse,
)
async def get_skill_profile(
    session_id: str, actor: ActorDep, service: SkillServiceDep
) -> SkillProfileResponse:
    try:
        return _skill_profile(await service.get_profile(actor, session_id))
    except Exception as exc:
        raise _translate(exc) from exc


@router.put(
    "/agent-sessions/{session_id}/skill-profile",
    response_model=SkillProfileResponse,
)
async def update_skill_profile(
    session_id: str,
    body: SkillProfileUpdateRequest,
    actor: ActorDep,
    service: SkillServiceDep,
) -> SkillProfileResponse:
    try:
        return _skill_profile(
            await service.update_profile(
                actor,
                session_id,
                expected_revision=body.expected_revision,
                selections=tuple(
                    SkillProfileSelection(item.source, item.skill_id, item.version)
                    for item in body.selections
                ),
            )
        )
    except ValueError as exc:
        raise HTTPException(422, "skill_configuration_invalid") from exc
    except Exception as exc:
        raise _translate(exc) from exc


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
        values = await service.list_message_views(actor, session_id)
        return [
            MessageResponse(
                message_id=x.message.message_id,
                session_id=x.message.session_id,
                sequence=x.message.sequence,
                role=x.message.role.value,
                content=x.message.content,
                turn_run_id=x.message.turn_run_id,
                claim_set_id=x.message.claim_set_id,
                created_at=x.message.created_at,
                claims=(
                    [
                        ClaimResponse(
                            text=claim.text,
                            citations=[
                                CitationResponse(
                                    evidence_id=citation.evidence_id,
                                    paper_id=citation.paper_id,
                                    version_id=citation.version_id,
                                    section_path=citation.section_path,
                                    page_start=citation.page_start,
                                    page_end=citation.page_end,
                                    excerpt=citation.excerpt,
                                )
                                for citation in claim.citations
                            ],
                        )
                        for claim in x.claims
                    ]
                    if x.claims is not None
                    else None
                ),
                attachment_ids=list(x.message.attachment_ids),
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
            attachment_ids=tuple(body.attachment_ids),
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
            attachment_refs=[
                {
                    "attachment_id": r.attachment_id,
                    "version": r.version,
                    "content_hash": r.content_hash,
                    "size_bytes": r.size_bytes,
                    "media_type": r.media_type,
                    "display_name": r.display_name,
                }
                for r in value.context_snapshot.attachment_refs
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


@router.get(
    "/agent-turn-runs/{run_id}/tool-executions",
    response_model=AgentToolExecutionsResponse,
)
async def list_tool_executions(
    run_id: str, actor: ActorDep, service: ServiceDep
) -> AgentToolExecutionsResponse:
    try:
        view = await service.list_tool_executions(actor, run_id)
    except Exception as exc:
        raise _translate(exc) from exc
    usage = view.usage
    return AgentToolExecutionsResponse(
        usage=AgentTurnUsageResponse(
            **{name: getattr(usage, name) for name in AgentTurnUsageResponse.model_fields}
        ),
        items=[
            AgentToolExecutionResponse(
                invocation_id=item.invocation_id,
                tool_name=item.tool_name,
                tool_version=item.tool_version,
                input_schema_hash=item.input_schema_hash,
                args_hash=item.args_hash,
                status=item.status.value,
                input_size_bytes=item.input_size_bytes,
                output_size_bytes=item.output_size_bytes,
                result_hash=item.result_hash,
                error_code=item.error_code,
                safe_message=item.safe_message,
                duration_ms=item.duration_ms,
                started_at=item.started_at,
                completed_at=item.completed_at,
            )
            for item in view.items
        ],
    )


@router.get(
    "/agent-turn-runs/{run_id}/artifacts",
    response_model=list[AgentArtifactResponse],
)
async def list_agent_artifacts(
    run_id: str,
    actor: ActorDep,
    service: AgentArtifactServiceDep,
) -> list[AgentArtifactResponse]:
    try:
        values = await service.list_artifacts(actor.owner_id, run_id)
    except AgentTurnNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_turn_not_found") from exc
    return [
        AgentArtifactResponse(
            artifact_id=value.artifact_id,
            turn_run_id=value.turn_run_id,
            name=value.name,
            media_type=value.media_type,
            content_hash=value.content_hash,
            size_bytes=value.size_bytes,
            previewable=value.previewable,
            created_at=value.created_at,
        )
        for value in values
    ]


@router.get("/agent-artifacts/{artifact_id}/content", response_class=Response)
async def get_agent_artifact_content(
    artifact_id: str,
    actor: ActorDep,
    service: AgentArtifactServiceDep,
) -> Response:
    try:
        result = await service.content(actor.owner_id, artifact_id)
    except AgentArtifactNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_artifact_not_found") from exc
    except AgentArtifactServiceError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.temporary
            else status.HTTP_409_CONFLICT,
            exc.code,
        ) from exc
    artifact = result.artifact
    disposition = "inline" if artifact.previewable else "attachment"
    fallback = "artifact" + artifact.name[artifact.name.rfind(".") :]
    encoded_name = quote(artifact.name, safe="")
    return Response(
        content=result.content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded_name}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
