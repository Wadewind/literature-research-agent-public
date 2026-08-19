"""Paper Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.domain.paper import Paper
from literature_agent.infrastructure.persistence.models import PaperORM


def _to_domain(orm: PaperORM) -> Paper:
    """将 ORM 模型转换为领域实体。"""
    return Paper(
        paper_id=orm.paper_id,
        owner_id=orm.owner_id,
        project_id=orm.project_id,
        created_at=orm.created_at,
    )


def _to_orm(paper: Paper) -> PaperORM:
    """将领域实体转换为 ORM 模型。"""
    return PaperORM(
        paper_id=paper.paper_id,
        owner_id=paper.owner_id,
        project_id=paper.project_id,
        created_at=paper.created_at,
    )


class SqlalchemyPaperRepository(PaperRepository):
    """基于 SQLAlchemy AsyncSession 的 PaperRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, paper: Paper) -> Paper:
        """保存 Paper。"""
        self._session.add(_to_orm(paper))
        return paper

    async def get_by_id(self, paper_id: str) -> Paper | None:
        """按 ID 查询 Paper。"""
        result = await self._session.execute(
            select(PaperORM).where(PaperORM.paper_id == paper_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list_by_project(self, project_id: str) -> list[Paper]:
        """按 Project ID 列出所有 Paper。"""
        result = await self._session.execute(
            select(PaperORM)
            .where(PaperORM.project_id == project_id)
            .order_by(PaperORM.created_at.desc()),
        )
        return [_to_domain(row) for row in result.scalars().all()]
