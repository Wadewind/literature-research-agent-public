"""Event Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.domain.event import Event
from literature_agent.infrastructure.persistence.models import EventORM


def _to_domain(orm: EventORM) -> Event:
    """将 ORM 模型转换为领域值对象。"""
    return Event(
        event_id=orm.event_id,
        event_version=orm.event_version,
        event_type=orm.event_type,
        run_id=orm.run_id,
        sequence=orm.sequence,
        occurred_at=orm.occurred_at,
        actor_type=orm.actor_type,
        correlation_id=orm.correlation_id,
        payload=orm.payload,
    )


def _to_orm(event: Event) -> EventORM:
    """将领域值对象转换为 ORM 模型。"""
    return EventORM(
        event_id=event.event_id,
        event_version=event.event_version,
        event_type=event.event_type,
        run_id=event.run_id,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type,
        correlation_id=event.correlation_id,
        payload=event.payload,
    )


class SqlalchemyEventRepository(EventRepository):
    """基于 SQLAlchemy AsyncSession 的 EventRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, event: Event) -> Event:
        """保存 Event。"""
        self._session.add(_to_orm(event))
        return event

    async def list_by_run(self, run_id: str) -> list[Event]:
        """按 Run ID 列出所有 Event，按 sequence 升序。"""
        result = await self._session.execute(
            select(EventORM).where(EventORM.run_id == run_id).order_by(EventORM.sequence.asc()),
        )
        return [_to_domain(row) for row in result.scalars().all()]
