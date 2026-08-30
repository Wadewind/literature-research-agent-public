"""Paper Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.domain.paper import Paper, PaperTitleSource
from literature_agent.infrastructure.persistence.models import PaperORM


def _to_domain(orm: PaperORM) -> Paper:
    """将 ORM 模型转换为领域实体。"""
    return Paper(
        paper_id=orm.paper_id,
        owner_id=orm.owner_id,
        created_at=orm.created_at,
        archived_at=orm.archived_at,
        title=orm.title,
        title_source=PaperTitleSource(orm.title_source) if orm.title_source else None,
    )


def _to_orm(paper: Paper) -> PaperORM:
    """将领域实体转换为 ORM 模型。"""
    return PaperORM(
        paper_id=paper.paper_id,
        owner_id=paper.owner_id,
        created_at=paper.created_at,
        archived_at=paper.archived_at,
        title=paper.title,
        title_source=paper.title_source.value if paper.title_source else None,
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

    async def update(self, paper: Paper) -> None:
        """按主键更新 Paper 的可变字段。"""
        orm = await self._session.get(PaperORM, paper.paper_id)
        if orm is None:
            return
        orm.archived_at = paper.archived_at
        orm.title = paper.title
        orm.title_source = paper.title_source.value if paper.title_source else None

    async def get_by_id(self, paper_id: str) -> Paper | None:
        """按 ID 查询 Paper。"""
        result = await self._session.execute(
            select(PaperORM).where(PaperORM.paper_id == paper_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list_by_owner(
        self,
        owner_id: str,
        include_archived: bool = False,
    ) -> list[Paper]:
        """列出 owner 个人文献库中的 Paper；默认排除已归档。"""
        statement = select(PaperORM).where(
            PaperORM.owner_id == owner_id,
            PaperORM.merged_into_paper_id.is_(None),
        )
        if not include_archived:
            statement = statement.where(PaperORM.archived_at.is_(None))
        result = await self._session.execute(
            statement.order_by(PaperORM.created_at.desc()),
        )
        return [_to_domain(row) for row in result.scalars().all()]
