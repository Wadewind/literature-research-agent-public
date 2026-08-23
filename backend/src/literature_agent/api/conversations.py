"""Conversation 与 Evidence 相关的 HTTP 路由（切片 8）。

端点：

- ``POST /api/v1/projects/{project_id}/conversations``：创建会话；
- ``GET  /api/v1/projects/{project_id}/conversations``：列出会话；
- ``GET  /api/v1/conversations/{conversation_id}``：会话详情；
- ``GET  /api/v1/conversations/{conversation_id}/messages``：消息列表
  （assistant 消息携带 Claim 与 Evidence 摘要，供前端直接渲染引用）；
- ``POST /api/v1/conversations/{conversation_id}/messages``：提交提问
  （``Idempotency-Key`` 必填，202 返回 ``{user_message_id, run_id,
  status: "queued"}``）；
- ``GET  /api/v1/projects/{project_id}/evidence/{evidence_id}``：
  Evidence 详情（excerpt + version_id，供 PDF 页码跳转）。

错误码沿用切片 2 定稿：404 ``conversation_not_found`` /
``evidence_not_found``；409 ``project_archived`` /
``conversation_busy`` / ``project_not_indexed``；422 ``invalid_scope``。
Run 查询、取消与 SSE 复用 ``/api/v1/runs/{run_id}`` 现有接口。
"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from literature_agent.api.dependencies import ActorDep, CorrelationIdDep
from literature_agent.application.conversation_service import (
    ConversationService,
    ConversationView,
    MessageView,
    PostMessageResult,
)
from literature_agent.domain.conversation import (
    CONVERSATION_TITLE_MAX_LENGTH,
    MESSAGE_CONTENT_MAX_LENGTH,
    Message,
)
from literature_agent.domain.evidence import Evidence
from literature_agent.domain.exceptions import (
    ConversationBusyError,
    ConversationNotFoundError,
    EvidenceNotFoundError,
    IdempotencyConflictError,
    InvalidScopeError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ProjectNotIndexedError,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.conversation_repository import (
    SqlalchemyConversationRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.message_repository import (
    SqlalchemyMessageRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)

router = APIRouter(prefix="/api/v1", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    """创建 Conversation 的请求体。"""

    title: str | None = Field(default=None, max_length=CONVERSATION_TITLE_MAX_LENGTH)
    scope_mode: str = Field(..., min_length=1, max_length=30)
    paper_ids: list[str] | None = Field(default=None, max_length=100)


class ScopePaperResponse(BaseModel):
    """固化的默认范围条目。"""

    paper_id: str
    version_id: str


class ConversationResponse(BaseModel):
    """Conversation 的响应模型（含解析后的范围版本列表）。"""

    conversation_id: str
    project_id: str
    owner_id: str
    title: str | None
    scope_mode: str
    active_run_id: str | None
    created_at: datetime
    scope_papers: list[ScopePaperResponse]


class MessageCreateRequest(BaseModel):
    """提交提问的请求体。"""

    content: str = Field(..., min_length=1, max_length=MESSAGE_CONTENT_MAX_LENGTH)


class PostMessageResponse(BaseModel):
    """提交提问的响应模型。"""

    user_message_id: str
    run_id: str
    status: str


class CitationResponse(BaseModel):
    """一条引用的响应模型（Evidence 摘要，供前端直接渲染）。"""

    evidence_id: str
    paper_id: str
    version_id: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    excerpt: str


class ClaimResponse(BaseModel):
    """一条 Claim 的响应模型（文本 + 引用）。"""

    text: str
    citations: list[CitationResponse]


class MessageResponse(BaseModel):
    """Message 的响应模型；assistant 消息携带 Claim 与引用。"""

    message_id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    run_id: str | None
    claim_set_id: str | None
    created_at: datetime
    claims: list[ClaimResponse] | None


class EvidenceResponse(BaseModel):
    """Evidence 详情的响应模型。"""

    evidence_id: str
    run_id: str
    project_id: str
    paper_id: str
    version_id: str
    parse_revision_id: str
    chunk_id: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    excerpt: str
    created_at: datetime


async def get_conversation_service(request: Request) -> ConversationService:
    """从应用状态构建 ConversationService。"""
    app_state = request.app.state.app_state
    return ConversationService(
        session_factory=app_state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        conversation_repo_factory=SqlalchemyConversationRepository,
        message_repo_factory=SqlalchemyMessageRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        event_notifier=app_state.event_notifier,
    )


ConversationServiceDep = Annotated[
    ConversationService, Depends(get_conversation_service)
]


def _conversation_response(view: ConversationView) -> ConversationResponse:
    """组装 Conversation 响应（含固化的范围版本列表）。"""
    conversation = view.conversation
    return ConversationResponse(
        conversation_id=conversation.conversation_id,
        project_id=conversation.project_id,
        owner_id=conversation.owner_id,
        title=conversation.title,
        scope_mode=conversation.scope_mode.value,
        active_run_id=conversation.active_run_id,
        created_at=conversation.created_at,
        scope_papers=[
            ScopePaperResponse(paper_id=entry.paper_id, version_id=entry.version_id)
            for entry in view.scope_papers
        ],
    )


def _message_response(view: MessageView) -> MessageResponse:
    """组装 Message 响应（assistant 消息携带 Claim 与引用摘要）。"""
    message: Message = view.message
    claims = None
    if view.claims is not None:
        claims = [
            ClaimResponse(
                text=claim.text,
                citations=[CitationResponse(**asdict(c)) for c in claim.citations],
            )
            for claim in view.claims
        ]
    return MessageResponse(
        message_id=message.message_id,
        conversation_id=message.conversation_id,
        sequence=message.sequence,
        role=message.role.value,
        content=message.content,
        run_id=message.run_id,
        claim_set_id=message.claim_set_id,
        created_at=message.created_at,
        claims=claims,
    )


@router.post(
    "/projects/{project_id}/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationResponse,
)
async def create_conversation(
    project_id: str,
    body: ConversationCreateRequest,
    actor: ActorDep,
    service: ConversationServiceDep,
) -> ConversationResponse:
    """在 Project 内创建 Conversation（scope 创建后不可修改）。"""
    try:
        view = await service.create_conversation(
            actor,
            project_id,
            title=body.title,
            scope_mode=body.scope_mode,
            paper_ids=body.paper_ids,
        )
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    except ProjectArchivedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project_archived",
        ) from None
    except InvalidScopeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_scope",
        ) from None
    return _conversation_response(view)


@router.get(
    "/projects/{project_id}/conversations",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    project_id: str,
    actor: ActorDep,
    service: ConversationServiceDep,
) -> list[ConversationResponse]:
    """列出 Project 的会话。"""
    try:
        conversations = await service.list_conversations(actor, project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    return [
        _conversation_response(ConversationView(conversation=c, scope_papers=[]))
        for c in conversations
    ]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
async def get_conversation(
    conversation_id: str,
    actor: ActorDep,
    service: ConversationServiceDep,
) -> ConversationResponse:
    """获取会话详情（含固化的默认范围）。"""
    try:
        view = await service.get_conversation(actor, conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation_not_found",
        ) from None
    return _conversation_response(view)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def list_messages(
    conversation_id: str,
    actor: ActorDep,
    service: ConversationServiceDep,
) -> list[MessageResponse]:
    """列出会话消息（sequence 升序，assistant 携带引用摘要）。"""
    try:
        views = await service.list_messages(actor, conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation_not_found",
        ) from None
    return [_message_response(view) for view in views]


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PostMessageResponse,
)
async def post_message(
    conversation_id: str,
    body: MessageCreateRequest,
    actor: ActorDep,
    service: ConversationServiceDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PostMessageResponse:
    """提交提问：创建 User Message 与 rag_answer Run（后台回答）。"""
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少 Idempotency-Key 请求头",
        )
    try:
        result: PostMessageResult = await service.post_message(
            actor,
            conversation_id,
            content=body.content,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
    except ConversationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation_not_found",
        ) from None
    except ProjectArchivedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project_archived",
        ) from None
    except ConversationBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conversation_busy",
        ) from None
    except ProjectNotIndexedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project_not_indexed",
        ) from None
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return PostMessageResponse(
        user_message_id=result.user_message_id,
        run_id=result.run_id,
        status=result.status,
    )


@router.get(
    "/projects/{project_id}/evidence/{evidence_id}",
    response_model=EvidenceResponse,
)
async def get_evidence(
    project_id: str,
    evidence_id: str,
    actor: ActorDep,
    service: ConversationServiceDep,
) -> EvidenceResponse:
    """查询 Evidence 详情（含 excerpt 与 version_id，供 PDF 跳转）。"""
    try:
        evidence: Evidence = await service.get_evidence(
            actor, project_id, evidence_id
        )
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    except EvidenceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="evidence_not_found",
        ) from None
    return EvidenceResponse(**asdict(evidence))
