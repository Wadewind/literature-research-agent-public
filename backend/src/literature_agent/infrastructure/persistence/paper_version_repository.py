"""PaperVersion Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.domain.paper_version import PaperVersion
from literature_agent.infrastructure.persistence.models import PaperVersionORM


def _to_domain(orm: PaperVersionORM) -> PaperVersion:
    """将 ORM 模型转换为领域实体。"""
    return PaperVersion(
        version_id=orm.version_id,
        paper_id=orm.paper_id,
        file_hash=orm.file_hash,
        storage_key=orm.storage_key,
        size_bytes=orm.size_bytes,
        content_type=orm.content_type,
        created_at=orm.created_at,
    )


def _to_orm(version: PaperVersion) -> PaperVersionORM:
    """将领域实体转换为 ORM 模型。"""
    return PaperVersionORM(
        version_id=version.version_id,
        paper_id=version.paper_id,
        file_hash=version.file_hash,
        storage_key=version.storage_key,
        size_bytes=version.size_bytes,
        content_type=version.content_type,
        created_at=version.created_at,
    )


class SqlalchemyPaperVersionRepository(PaperVersionRepository):
    """基于 SQLAlchemy AsyncSession 的 PaperVersionRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, version: PaperVersion) -> PaperVersion:
        """保存 PaperVersion。"""
        self._session.add(_to_orm(version))
        return version

    async def get_by_id(self, version_id: str) -> PaperVersion | None:
        """按 ID 查询 PaperVersion。"""
        result = await self._session.execute(
            select(PaperVersionORM).where(PaperVersionORM.version_id == version_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list_by_paper(self, paper_id: str) -> list[PaperVersion]:
        """按 Paper ID 列出所有版本。"""
        result = await self._session.execute(
            select(PaperVersionORM)
            .where(PaperVersionORM.paper_id == paper_id)
            .order_by(PaperVersionORM.created_at.desc()),
        )
        return [_to_domain(row) for row in result.scalars().all()]
