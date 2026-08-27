"""ChunkSet Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.domain.chunk import ChunkSet, ChunkSetStatus
from literature_agent.infrastructure.persistence.models import (
    ChunkSetORM,
    DocumentParseRevisionORM,
    PaperORM,
    PaperVersionORM,
    ProjectORM,
    ProjectPaperORM,
)


def _to_domain(orm: ChunkSetORM) -> ChunkSet:
    """将 ORM 模型转换为领域实体。"""
    return ChunkSet(
        chunk_set_id=orm.chunk_set_id,
        parse_revision_id=orm.parse_revision_id,
        profile_hash=orm.profile_hash,
        status=ChunkSetStatus(orm.status),
        config=orm.config,
        error=orm.error,
        created_at=orm.created_at,
        completed_at=orm.completed_at,
    )


def _to_orm(chunk_set: ChunkSet) -> ChunkSetORM:
    """将领域实体转换为 ORM 模型。"""
    return ChunkSetORM(
        chunk_set_id=chunk_set.chunk_set_id,
        parse_revision_id=chunk_set.parse_revision_id,
        profile_hash=chunk_set.profile_hash,
        status=chunk_set.status.value,
        config=chunk_set.config,
        error=chunk_set.error,
        created_at=chunk_set.created_at,
        completed_at=chunk_set.completed_at,
    )


class SqlalchemyChunkSetRepository(ChunkSetRepository):
    """基于 SQLAlchemy AsyncSession 的 ChunkSetRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, chunk_set: ChunkSet) -> ChunkSet:
        """保存 ChunkSet。"""
        self._session.add(_to_orm(chunk_set))
        return chunk_set

    async def get_by_id(self, chunk_set_id: str) -> ChunkSet | None:
        """按 ID 查询 ChunkSet。"""
        result = await self._session.execute(
            select(ChunkSetORM).where(ChunkSetORM.chunk_set_id == chunk_set_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def get_by_revision_and_profile(
        self,
        parse_revision_id: str,
        profile_hash: str,
    ) -> ChunkSet | None:
        """按 Parse Revision 和 profile 哈希查询。"""
        result = await self._session.execute(
            select(ChunkSetORM).where(
                ChunkSetORM.parse_revision_id == parse_revision_id,
                ChunkSetORM.profile_hash == profile_hash,
            ),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def get_latest_by_revision(self, parse_revision_id: str) -> ChunkSet | None:
        """按 Parse Revision 查询最新创建的 ChunkSet。"""
        result = await self._session.execute(
            select(ChunkSetORM)
            .where(ChunkSetORM.parse_revision_id == parse_revision_id)
            .order_by(ChunkSetORM.created_at.desc())
            .limit(1),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def get_ready_by_version(self, version_id: str) -> ChunkSet | None:
        result = await self._session.execute(
            select(ChunkSetORM)
            .join(
                DocumentParseRevisionORM,
                ChunkSetORM.parse_revision_id == DocumentParseRevisionORM.revision_id,
            )
            .where(
                DocumentParseRevisionORM.version_id == version_id,
                ChunkSetORM.status == ChunkSetStatus.READY.value,
            )
            .order_by(ChunkSetORM.created_at.desc())
            .limit(1)
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def count_ready_by_version_ids(self, version_ids: list[str]) -> int:
        """统计给定 PaperVersion 集合下 ready ChunkSet 的数量。"""
        if not version_ids:
            return 0
        result = await self._session.execute(
            select(func.count())
            .select_from(ChunkSetORM)
            .join(
                DocumentParseRevisionORM,
                ChunkSetORM.parse_revision_id
                == DocumentParseRevisionORM.revision_id,
            )
            .where(
                DocumentParseRevisionORM.version_id.in_(version_ids),
                ChunkSetORM.status == ChunkSetStatus.READY.value,
            ),
        )
        return result.scalar_one()

    async def count_ready_project_versions_scoped(
        self,
        project_id: str,
        owner_id: str,
    ) -> int | None:
        """用单条 Project-scoped 查询统计当前可用索引。"""
        ready_count = (
            select(func.count(func.distinct(ProjectPaperORM.selected_version_id)))
            .select_from(ProjectPaperORM)
            .join(
                PaperORM,
                and_(
                    PaperORM.paper_id == ProjectPaperORM.paper_id,
                    PaperORM.owner_id == owner_id,
                    PaperORM.archived_at.is_(None),
                ),
            )
            .join(
                PaperVersionORM,
                and_(
                    PaperVersionORM.version_id
                    == ProjectPaperORM.selected_version_id,
                    PaperVersionORM.paper_id == ProjectPaperORM.paper_id,
                    PaperVersionORM.owner_id == owner_id,
                ),
            )
            .join(
                DocumentParseRevisionORM,
                DocumentParseRevisionORM.version_id == PaperVersionORM.version_id,
            )
            .join(
                ChunkSetORM,
                and_(
                    ChunkSetORM.parse_revision_id
                    == DocumentParseRevisionORM.revision_id,
                    ChunkSetORM.status == ChunkSetStatus.READY.value,
                ),
            )
            .where(ProjectPaperORM.project_id == ProjectORM.project_id)
            .correlate(ProjectORM)
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(ready_count).select_from(ProjectORM).where(
                ProjectORM.project_id == project_id,
                ProjectORM.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def save(self, chunk_set: ChunkSet) -> None:
        """保存 ChunkSet 状态更新。"""
        result = await self._session.execute(
            select(ChunkSetORM).where(ChunkSetORM.chunk_set_id == chunk_set.chunk_set_id),
        )
        orm = result.scalar_one()
        orm.status = chunk_set.status.value
        orm.error = chunk_set.error
        orm.completed_at = chunk_set.completed_at
