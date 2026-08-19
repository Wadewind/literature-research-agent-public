"""Queue Outbox Repository 的 PostgreSQL 适配器。"""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.domain.queue_outbox import OutboxStatus, QueueOutbox
from literature_agent.infrastructure.persistence.models import QueueOutboxORM


def _to_domain(orm: QueueOutboxORM) -> QueueOutbox:
    """将 ORM 模型转换为领域实体。"""
    return QueueOutbox(
        outbox_id=orm.outbox_id,
        run_id=orm.run_id,
        status=OutboxStatus(orm.status),
        attempt_count=orm.attempt_count,
        scheduled_at=orm.scheduled_at,
        dispatched_at=orm.dispatched_at,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def _to_orm(entry: QueueOutbox) -> QueueOutboxORM:
    """将领域实体转换为 ORM 模型。"""
    return QueueOutboxORM(
        outbox_id=entry.outbox_id,
        run_id=entry.run_id,
        status=entry.status.value,
        attempt_count=entry.attempt_count,
        scheduled_at=entry.scheduled_at,
        dispatched_at=entry.dispatched_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


class SqlalchemyOutboxRepository(OutboxRepository):
    """基于 SQLAlchemy AsyncSession 的 OutboxRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, entry: QueueOutbox) -> QueueOutbox:
        """保存 Outbox 记录。"""
        self._session.add(_to_orm(entry))
        return entry

    async def get_by_run_id(self, run_id: str) -> QueueOutbox | None:
        """按 Run ID 查询 Outbox 记录。"""
        result = await self._session.execute(
            select(QueueOutboxORM).where(QueueOutboxORM.run_id == run_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list_due_pending(self, now: datetime, limit: int) -> list[QueueOutbox]:
        """查询已到期的待派发记录，按预定时间升序。"""
        result = await self._session.execute(
            select(QueueOutboxORM)
            .where(
                QueueOutboxORM.status == OutboxStatus.PENDING.value,
                QueueOutboxORM.scheduled_at <= now,
            )
            .order_by(QueueOutboxORM.scheduled_at)
            .limit(limit),
        )
        return [_to_domain(orm) for orm in result.scalars().all()]

    async def try_mark_dispatched(self, outbox_id: str, dispatched_at: datetime) -> bool:
        """条件更新为已投递；仅 PENDING 状态可更新。"""
        if dispatched_at.tzinfo is None:
            dispatched_at = dispatched_at.replace(tzinfo=UTC)
        result = cast(
            CursorResult,
            await self._session.execute(
                update(QueueOutboxORM)
                .where(
                    QueueOutboxORM.outbox_id == outbox_id,
                    QueueOutboxORM.status == OutboxStatus.PENDING.value,
                )
                .values(
                    status=OutboxStatus.DISPATCHED.value,
                    dispatched_at=dispatched_at,
                    updated_at=dispatched_at,
                ),
            ),
        )
        return result.rowcount == 1

    async def save(self, entry: QueueOutbox) -> None:
        """保存 Outbox 记录的更新。"""
        result = await self._session.execute(
            select(QueueOutboxORM).where(QueueOutboxORM.outbox_id == entry.outbox_id),
        )
        orm = result.scalar_one()
        orm.status = entry.status.value
        orm.attempt_count = entry.attempt_count
        orm.scheduled_at = entry.scheduled_at
        orm.dispatched_at = entry.dispatched_at
        orm.updated_at = entry.updated_at

    async def reset_for_retry(self, run_id: str, scheduled_at: datetime) -> bool:
        """条件重置为待投递；仅 DISPATCHED 状态可更新。"""
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        result = cast(
            CursorResult,
            await self._session.execute(
                update(QueueOutboxORM)
                .where(
                    QueueOutboxORM.run_id == run_id,
                    QueueOutboxORM.status == OutboxStatus.DISPATCHED.value,
                )
                .values(
                    status=OutboxStatus.PENDING.value,
                    attempt_count=QueueOutboxORM.attempt_count + 1,
                    scheduled_at=scheduled_at,
                    dispatched_at=None,
                    updated_at=now,
                ),
            ),
        )
        return result.rowcount == 1
