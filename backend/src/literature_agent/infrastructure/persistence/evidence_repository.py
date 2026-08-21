"""Evidence Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.domain.evidence import Evidence
from literature_agent.infrastructure.persistence.models import EvidenceORM


def _to_domain(orm: EvidenceORM) -> Evidence:
    """将 ORM 模型转换为领域实体。"""
    return Evidence(
        evidence_id=orm.evidence_id,
        run_id=orm.run_id,
        project_id=orm.project_id,
        paper_id=orm.paper_id,
        version_id=orm.version_id,
        parse_revision_id=orm.parse_revision_id,
        chunk_id=orm.chunk_id,
        section_path=orm.section_path,
        page_start=orm.page_start,
        page_end=orm.page_end,
        excerpt=orm.excerpt,
        created_at=orm.created_at,
    )


def _to_orm(evidence: Evidence) -> EvidenceORM:
    """将领域实体转换为 ORM 模型。"""
    return EvidenceORM(
        evidence_id=evidence.evidence_id,
        run_id=evidence.run_id,
        project_id=evidence.project_id,
        paper_id=evidence.paper_id,
        version_id=evidence.version_id,
        parse_revision_id=evidence.parse_revision_id,
        chunk_id=evidence.chunk_id,
        section_path=evidence.section_path,
        page_start=evidence.page_start,
        page_end=evidence.page_end,
        excerpt=evidence.excerpt,
        created_at=evidence.created_at,
    )


class SqlalchemyEvidenceRepository(EvidenceRepository):
    """基于 SQLAlchemy AsyncSession 的 EvidenceRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add_many(self, evidence: list[Evidence]) -> None:
        """批量固化 Evidence。"""
        self._session.add_all([_to_orm(e) for e in evidence])

    async def list_by_run(self, run_id: str) -> list[Evidence]:
        """按 Run 查询 Evidence，按创建时间升序返回。"""
        result = await self._session.execute(
            select(EvidenceORM)
            .where(EvidenceORM.run_id == run_id)
            .order_by(EvidenceORM.created_at),
        )
        return [_to_domain(row) for row in result.scalars().all()]

    async def list_by_ids(self, evidence_ids: list[str]) -> list[Evidence]:
        """按 ID 列表查询 Evidence。"""
        if not evidence_ids:
            return []
        result = await self._session.execute(
            select(EvidenceORM).where(EvidenceORM.evidence_id.in_(evidence_ids)),
        )
        return [_to_domain(row) for row in result.scalars().all()]
