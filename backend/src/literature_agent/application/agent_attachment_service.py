"""AgentSession 输入附件上传、列表与受限删除。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TypeVar

from literature_agent.application.ports.agent_attachment_repository import (
    AgentAttachmentRepository,
)
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage, StorageError
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.agent_attachment import (
    AgentAttachment,
    AgentAttachmentStatus,
    agent_attachment_request_hash,
    create_agent_attachment,
    validate_agent_attachment_content,
)
from literature_agent.domain.exceptions import (
    AgentAttachmentNotFoundError,
    AgentAttachmentReferencedError,
    AgentSessionNotFoundError,
    IdempotencyConflictError,
)

TSession = TypeVar("TSession", bound=Session)


class AgentAttachmentStorageError(Exception):
    """业务 Storage 写入失败；上传可使用同一幂等键重试。"""


@dataclass(frozen=True, slots=True)
class AgentAttachmentUploadResult:
    attachment: AgentAttachment
    replayed: bool


class AgentAttachmentService[TSession: Session]:
    """保证 Storage I/O 不位于数据库事务内。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        attachment_repo_factory: Callable[[TSession], AgentAttachmentRepository],
        storage: Storage,
    ) -> None:
        self._session_factory = session_factory
        self._agent_repo_factory = agent_repo_factory
        self._attachment_repo_factory = attachment_repo_factory
        self._storage = storage

    async def upload(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        display_name: str,
        media_type: str,
        content: bytes,
        idempotency_key: str,
    ) -> AgentAttachmentUploadResult:
        validated = validate_agent_attachment_content(
            display_name=display_name, media_type=media_type, content=content
        )
        request_hash = agent_attachment_request_hash(
            session_id=session_id,
            display_name=validated.display_name,
            media_type=validated.media_type,
            content_hash=validated.content_hash,
        )
        async with self._session_factory() as db:
            session = await self._agent_repo_factory(db).get_session_scoped(
                session_id, actor.owner_id
            )
            if session is None:
                raise AgentSessionNotFoundError(session_id)
            repo = self._attachment_repo_factory(db)
            existing = await repo.get_by_idempotency_key(actor.owner_id, idempotency_key)
            if existing is not None:
                if existing.session_id != session_id or existing.request_hash != request_hash:
                    raise IdempotencyConflictError(idempotency_key)
                return AgentAttachmentUploadResult(existing, True)
            value = create_agent_attachment(
                owner_id=actor.owner_id,
                project_id=session.project_id,
                session_id=session_id,
                display_name=validated.display_name,
                media_type=validated.media_type,
                content_hash=validated.content_hash,
                size_bytes=validated.size_bytes,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        try:
            await self._storage.write(value.storage_key, content)
        except StorageError as exc:
            raise AgentAttachmentStorageError("attachment_storage_write_failed") from exc
        async with self._session_factory() as db:
            session = await self._agent_repo_factory(db).get_session_scoped(
                session_id, actor.owner_id
            )
            if session is None or session.project_id != value.project_id:
                raise AgentSessionNotFoundError(session_id)
            repo = self._attachment_repo_factory(db)
            existing = await repo.get_by_idempotency_key(actor.owner_id, idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError(idempotency_key)
                return AgentAttachmentUploadResult(existing, True)
            result = await repo.add_if_absent(value)
            if (
                result.attachment.session_id != session_id
                or result.attachment.request_hash != request_hash
            ):
                raise IdempotencyConflictError(idempotency_key)
            await db.commit()
            return AgentAttachmentUploadResult(result.attachment, replayed=not result.inserted)

    async def list(self, actor: ActorContext, session_id: str) -> list[AgentAttachment]:
        async with self._session_factory() as db:
            session = await self._agent_repo_factory(db).get_session_scoped(
                session_id, actor.owner_id
            )
            if session is None:
                raise AgentSessionNotFoundError(session_id)
            return await self._attachment_repo_factory(db).list_scoped(session_id, actor.owner_id)

    async def delete(self, actor: ActorContext, session_id: str, attachment_id: str) -> None:
        async with self._session_factory() as db:
            repo = self._attachment_repo_factory(db)
            value = await repo.get_scoped(
                attachment_id,
                session_id,
                actor.owner_id,
                for_update=True,
            )
            if value is None or value.status is not AgentAttachmentStatus.AVAILABLE:
                raise AgentAttachmentNotFoundError(attachment_id)
            if await repo.is_referenced(attachment_id):
                raise AgentAttachmentReferencedError(attachment_id)
            if not await repo.mark_deleted(value.delete()):
                raise AgentAttachmentNotFoundError(attachment_id)
            await db.commit()
