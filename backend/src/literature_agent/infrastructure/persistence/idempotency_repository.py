"""IdempotencyKey Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRepository,
)


def _to_orm(record: IdempotencyRecord) -> object:
    """将领域记录转换为 ORM 模型。

    延迟导入避免循环依赖。
    """
    from literature_agent.infrastructure.persistence.models import IdempotencyKeyORM

    return IdempotencyKeyORM(
        owner_id=record.owner_id,
        idempotency_key=record.idempotency_key,
        project_id=record.project_id,
        request_hash=record.request_hash,
        run_id=record.run_id,
        paper_id=record.paper_id or None,
        version_id=record.version_id or None,
        status=record.status,
        reused=record.reused,
        already_added=record.already_added,
    )


class SqlalchemyIdempotencyRepository(IdempotencyRepository):
    """基于 SQLAlchemy AsyncSession 的 IdempotencyRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def get(self, owner_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        """按 owner_id 和 idempotency_key 查询记录。"""
        from literature_agent.infrastructure.persistence.models import IdempotencyKeyORM

        result = await self._session.execute(
            select(IdempotencyKeyORM).where(
                IdempotencyKeyORM.owner_id == owner_id,
                IdempotencyKeyORM.idempotency_key == idempotency_key,
            ),
        )
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return IdempotencyRecord(
            owner_id=orm.owner_id,
            idempotency_key=orm.idempotency_key,
            project_id=orm.project_id,
            request_hash=orm.request_hash,
            run_id=orm.run_id,
            paper_id=orm.paper_id or "",
            version_id=orm.version_id or "",
            status=orm.status,
            reused=orm.reused,
            already_added=orm.already_added,
        )

    async def add(self, record: IdempotencyRecord) -> IdempotencyRecord:
        """保存幂等键记录。

        违反唯一约束时抛出 ``IntegrityError``，由调用方处理。
        """
        self._session.add(_to_orm(record))
        try:
            await self._session.flush()
        except IntegrityError:
            raise
        return record
