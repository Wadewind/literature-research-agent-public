"""AgentSession 输入附件 API；不暴露 Storage 与 Sandbox 物理路径。"""

from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.agent_attachment_service import (
    AgentAttachmentService,
    AgentAttachmentStorageError,
)
from literature_agent.domain.agent_attachment import (
    AGENT_ATTACHMENT_MAX_BYTES,
    AgentAttachment,
    AgentAttachmentValidationError,
)
from literature_agent.domain.exceptions import (
    AgentAttachmentNotFoundError,
    AgentAttachmentReferencedError,
    AgentSessionNotFoundError,
    IdempotencyConflictError,
)
from literature_agent.infrastructure.persistence.agent_attachment_repository import (
    SqlalchemyAgentAttachmentRepository,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)

router = APIRouter(prefix="/api/v1", tags=["agent-attachments"])


class AgentAttachmentResponse(BaseModel):
    attachment_id: str
    session_id: str
    version: int
    display_name: str
    media_type: str
    content_hash: str
    size_bytes: int
    status: str
    created_at: datetime


async def get_service(request: Request) -> AgentAttachmentService:
    state = request.app.state.app_state
    return AgentAttachmentService(
        session_factory=state.session_factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        attachment_repo_factory=SqlalchemyAgentAttachmentRepository,
        storage=state.storage,
    )


ServiceDep = Annotated[AgentAttachmentService, Depends(get_service)]


def _response(value: AgentAttachment) -> AgentAttachmentResponse:
    return AgentAttachmentResponse(
        attachment_id=value.attachment_id,
        session_id=value.session_id,
        version=value.version,
        display_name=value.display_name,
        media_type=value.media_type,
        content_hash=value.content_hash,
        size_bytes=value.size_bytes,
        status=value.status.value,
        created_at=value.created_at,
    )


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, (AgentSessionNotFoundError, AgentAttachmentNotFoundError)):
        return HTTPException(status.HTTP_404_NOT_FOUND, "agent_attachment_not_found")
    if isinstance(exc, (IdempotencyConflictError, AgentAttachmentReferencedError)):
        code = (
            "attachment_referenced"
            if isinstance(exc, AgentAttachmentReferencedError)
            else "idempotency_conflict"
        )
        return HTTPException(status.HTTP_409_CONFLICT, code)
    if isinstance(exc, AgentAttachmentValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code)
    if isinstance(exc, AgentAttachmentStorageError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    raise exc


@router.post(
    "/agent-sessions/{session_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    response_model=AgentAttachmentResponse,
)
async def upload_attachment(
    session_id: str,
    actor: ActorDep,
    service: ServiceDep,
    file: Annotated[UploadFile, File()],
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentAttachmentResponse:
    if idempotency_key is None or not idempotency_key.strip() or len(idempotency_key) > 255:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "idempotency_key_invalid")
    content = await file.read(AGENT_ATTACHMENT_MAX_BYTES + 1)
    if len(content) > AGENT_ATTACHMENT_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "attachment_too_large")
    try:
        result = await service.upload(
            actor,
            session_id,
            display_name=file.filename or "",
            media_type=file.content_type or "",
            content=content,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return _response(result.attachment)


@router.get(
    "/agent-sessions/{session_id}/attachments",
    response_model=list[AgentAttachmentResponse],
)
async def list_attachments(
    session_id: str, actor: ActorDep, service: ServiceDep
) -> list[AgentAttachmentResponse]:
    try:
        return [_response(value) for value in await service.list(actor, session_id)]
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete(
    "/agent-sessions/{session_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    session_id: str,
    attachment_id: str,
    actor: ActorDep,
    service: ServiceDep,
) -> None:
    try:
        await service.delete(actor, session_id, attachment_id)
    except Exception as exc:
        raise _translate(exc) from exc
