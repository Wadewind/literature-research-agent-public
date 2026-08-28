"""AgentAttachment PostgreSQL Repository。"""

from typing import cast

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.agent_attachment_repository import (
    AgentAttachmentInsertResult,
    AgentAttachmentRepository,
)
from literature_agent.domain.agent_attachment import (
    AgentAttachment,
    AgentAttachmentStatus,
)
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.infrastructure.persistence.models import (
    AgentAttachmentORM,
    AgentMessageAttachmentORM,
    AgentSessionORM,
)


class SqlalchemyAgentAttachmentRepository(AgentAttachmentRepository):
    """所有读取都闭合 owner/Session/Project 关系。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_idempotency_key(
        self, owner_id: str, idempotency_key: str
    ) -> AgentAttachment | None:
        row = (
            await self._session.execute(
                select(AgentAttachmentORM).where(
                    AgentAttachmentORM.owner_id == owner_id,
                    AgentAttachmentORM.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        return _attachment(row) if row is not None else None

    async def add_if_absent(self, value: AgentAttachment) -> AgentAttachmentInsertResult:
        inserted = (
            await self._session.execute(
                insert(AgentAttachmentORM)
                .values(
                    attachment_id=value.attachment_id,
                    owner_id=value.owner_id,
                    project_id=value.project_id,
                    session_id=value.session_id,
                    version=value.version,
                    display_name=value.display_name,
                    media_type=value.media_type,
                    content_hash=value.content_hash,
                    size_bytes=value.size_bytes,
                    storage_key=value.storage_key,
                    idempotency_key=value.idempotency_key,
                    request_hash=value.request_hash,
                    status=value.status.value,
                    created_at=value.created_at,
                    deleted_at=value.deleted_at,
                )
                .on_conflict_do_nothing()
                .returning(AgentAttachmentORM)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return AgentAttachmentInsertResult(_attachment(inserted), True)
        found = await self.get_by_idempotency_key(value.owner_id, value.idempotency_key)
        if found is None:
            # 只可能是不可解释的 UUID/唯一约束碰撞；不能把其他 scope 事实泄漏给调用者。
            raise RunConcurrentModificationError(value.session_id)
        return AgentAttachmentInsertResult(found, False)

    async def list_scoped(self, session_id: str, owner_id: str) -> list[AgentAttachment]:
        rows = (
            (
                await self._session.execute(
                    select(AgentAttachmentORM)
                    .join(AgentSessionORM)
                    .where(
                        AgentAttachmentORM.session_id == session_id,
                        AgentAttachmentORM.owner_id == owner_id,
                        AgentSessionORM.owner_id == owner_id,
                        AgentAttachmentORM.project_id == AgentSessionORM.project_id,
                    )
                    .order_by(
                        AgentAttachmentORM.created_at,
                        AgentAttachmentORM.attachment_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_attachment(row) for row in rows]

    async def get_scoped(
        self,
        attachment_id: str,
        session_id: str,
        owner_id: str,
        *,
        for_update: bool = False,
    ) -> AgentAttachment | None:
        statement = (
            select(AgentAttachmentORM)
            .join(AgentSessionORM)
            .where(
                AgentAttachmentORM.attachment_id == attachment_id,
                AgentAttachmentORM.session_id == session_id,
                AgentAttachmentORM.owner_id == owner_id,
                AgentSessionORM.owner_id == owner_id,
                AgentAttachmentORM.project_id == AgentSessionORM.project_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=AgentAttachmentORM)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return _attachment(row) if row is not None else None

    async def get_many_available_scoped(
        self,
        attachment_ids: tuple[str, ...],
        session_id: str,
        owner_id: str,
        *,
        for_update: bool = False,
    ) -> list[AgentAttachment]:
        if not attachment_ids:
            return []
        statement = (
            select(AgentAttachmentORM)
            .join(AgentSessionORM)
            .where(
                AgentAttachmentORM.attachment_id.in_(attachment_ids),
                AgentAttachmentORM.session_id == session_id,
                AgentAttachmentORM.owner_id == owner_id,
                AgentAttachmentORM.status == AgentAttachmentStatus.AVAILABLE.value,
                AgentSessionORM.owner_id == owner_id,
                AgentAttachmentORM.project_id == AgentSessionORM.project_id,
            )
            # 多附件请求始终按稳定 ID 顺序获取行锁，再恢复为调用者请求顺序。
            .order_by(AgentAttachmentORM.attachment_id)
        )
        if for_update:
            statement = statement.with_for_update(of=AgentAttachmentORM)
        rows = (await self._session.execute(statement)).scalars().all()
        by_id = {row.attachment_id: _attachment(row) for row in rows}
        return [by_id[value] for value in attachment_ids if value in by_id]

    async def is_referenced(self, attachment_id: str) -> bool:
        return bool(
            await self._session.scalar(
                select(exists().where(AgentMessageAttachmentORM.attachment_id == attachment_id))
            )
        )

    async def mark_deleted(self, value: AgentAttachment) -> bool:
        if value.status is not AgentAttachmentStatus.DELETED:
            raise ValueError("mark_deleted 只接受 DELETED AgentAttachment")
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentAttachmentORM)
                .where(
                    AgentAttachmentORM.attachment_id == value.attachment_id,
                    AgentAttachmentORM.owner_id == value.owner_id,
                    AgentAttachmentORM.session_id == value.session_id,
                    AgentAttachmentORM.status == AgentAttachmentStatus.AVAILABLE.value,
                )
                .values(status=value.status.value, deleted_at=value.deleted_at)
            ),
        )
        return result.rowcount == 1


def _attachment(row: AgentAttachmentORM) -> AgentAttachment:
    return AgentAttachment(
        attachment_id=row.attachment_id,
        owner_id=row.owner_id,
        project_id=row.project_id,
        session_id=row.session_id,
        version=row.version,
        display_name=row.display_name,
        media_type=row.media_type,
        content_hash=row.content_hash,
        size_bytes=row.size_bytes,
        storage_key=row.storage_key,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        status=AgentAttachmentStatus(row.status),
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )
