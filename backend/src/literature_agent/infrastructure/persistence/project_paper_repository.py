"""ProjectPaper Repository 的 PostgreSQL 适配器。"""

from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.project_paper_repository import (
    ProjectPaperRepository,
)
from literature_agent.domain.project_paper import ProjectPaper
from literature_agent.infrastructure.persistence.models import ProjectPaperORM


def _to_domain(orm: ProjectPaperORM) -> ProjectPaper:
    """将 ORM 模型转换为领域实体。"""
    return ProjectPaper(
        project_id=orm.project_id,
        paper_id=orm.paper_id,
        selected_version_id=orm.selected_version_id,
        created_at=orm.created_at,
    )


class SqlalchemyProjectPaperRepository(ProjectPaperRepository):
    """基于 SQLAlchemy AsyncSession 的收录关系仓库。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, relation: ProjectPaper) -> ProjectPaper:
        """保存收录关系。"""
        self._session.add(
            ProjectPaperORM(
                project_id=relation.project_id,
                paper_id=relation.paper_id,
                selected_version_id=relation.selected_version_id,
                created_at=relation.created_at,
            )
        )
        return relation

    async def get(self, project_id: str, paper_id: str) -> ProjectPaper | None:
        """查询收录关系。"""
        orm = await self._session.get(ProjectPaperORM, (project_id, paper_id))
        return _to_domain(orm) if orm else None

    async def get_by_version(
        self,
        project_id: str,
        version_id: str,
    ) -> ProjectPaper | None:
        """按 Project 和 Version 查询收录关系。"""
        result = await self._session.execute(
            select(ProjectPaperORM).where(
                ProjectPaperORM.project_id == project_id,
                ProjectPaperORM.selected_version_id == version_id,
            )
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list_by_project(self, project_id: str) -> list[ProjectPaper]:
        """列出 Project 的收录关系。"""
        result = await self._session.execute(
            select(ProjectPaperORM)
            .where(ProjectPaperORM.project_id == project_id)
            .order_by(ProjectPaperORM.created_at.desc())
        )
        return [_to_domain(row) for row in result.scalars().all()]

    async def list_by_paper(self, paper_id: str) -> list[ProjectPaper]:
        """列出 Paper 的收录关系。"""
        result = await self._session.execute(
            select(ProjectPaperORM).where(ProjectPaperORM.paper_id == paper_id)
        )
        return [_to_domain(row) for row in result.scalars().all()]

    async def remove(self, project_id: str, paper_id: str) -> bool:
        """删除收录关系。"""
        result = cast(
            CursorResult,
            await self._session.execute(
                delete(ProjectPaperORM).where(
                    ProjectPaperORM.project_id == project_id,
                    ProjectPaperORM.paper_id == paper_id,
                )
            ),
        )
        return bool(result.rowcount)
