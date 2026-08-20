"""PaperVersion Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select, update
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
        owner_id=orm.owner_id,
        file_hash=orm.file_hash,
        storage_key=orm.storage_key,
        size_bytes=orm.size_bytes,
        content_type=orm.content_type,
        created_at=orm.created_at,
        current_parse_revision_id=orm.current_parse_revision_id,
        display_filename=orm.display_filename,
        ingestion_run_id=orm.ingestion_run_id,
    )


def _to_orm(version: PaperVersion) -> PaperVersionORM:
    """将领域实体转换为 ORM 模型。"""
    return PaperVersionORM(
        version_id=version.version_id,
        paper_id=version.paper_id,
        owner_id=version.owner_id,
        file_hash=version.file_hash,
        storage_key=version.storage_key,
        size_bytes=version.size_bytes,
        content_type=version.content_type,
        created_at=version.created_at,
        display_filename=version.display_filename,
        ingestion_run_id=version.ingestion_run_id,
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

    async def get_by_owner_and_hash(
        self,
        owner_id: str,
        file_hash: str,
    ) -> PaperVersion | None:
        """按 owner 与内容哈希查询可复用 Version。"""
        result = await self._session.execute(
            select(PaperVersionORM).where(
                PaperVersionORM.owner_id == owner_id,
                PaperVersionORM.file_hash == file_hash,
                PaperVersionORM.is_deduplication_canonical.is_(True),
            ),
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

    async def set_current_parse_revision(self, version_id: str, revision_id: str) -> None:
        """更新 Version 的当前 Parse Revision 指针。"""
        await self._session.execute(
            update(PaperVersionORM)
            .where(PaperVersionORM.version_id == version_id)
            .values(current_parse_revision_id=revision_id),
        )
