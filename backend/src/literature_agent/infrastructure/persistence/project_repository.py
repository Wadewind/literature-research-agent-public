"""Project Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.domain.project import Project
from literature_agent.infrastructure.persistence.models import ProjectORM


def _to_domain(orm: ProjectORM) -> Project:
    """将 ORM 模型转换为领域实体。"""
    return Project(
        project_id=orm.project_id,
        owner_id=orm.owner_id,
        name=orm.name,
        description=orm.description,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        archived_at=orm.archived_at,
    )


def _to_orm(project: Project) -> ProjectORM:
    """将领域实体转换为 ORM 模型。"""
    return ProjectORM(
        project_id=project.project_id,
        owner_id=project.owner_id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        archived_at=project.archived_at,
    )


class SqlalchemyProjectRepository(ProjectRepository):
    """基于 SQLAlchemy AsyncSession 的 ProjectRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, project: Project) -> Project:
        """保存 Project。"""
        self._session.add(_to_orm(project))
        return project

    async def update(self, project: Project) -> None:
        """按主键更新 Project 的可变字段。"""
        orm = await self._session.get(ProjectORM, project.project_id)
        if orm is None:
            return
        orm.name = project.name
        orm.description = project.description
        orm.updated_at = project.updated_at
        orm.archived_at = project.archived_at

    async def list_by_owner(
        self,
        owner_id: str,
        include_archived: bool = False,
    ) -> list[Project]:
        """按所有者列出 Project；默认排除已归档。"""
        statement = select(ProjectORM).where(ProjectORM.owner_id == owner_id)
        if not include_archived:
            statement = statement.where(ProjectORM.archived_at.is_(None))
        result = await self._session.execute(
            statement.order_by(ProjectORM.created_at.desc()),
        )
        return [_to_domain(row) for row in result.scalars().all()]

    async def get_by_id(self, project_id: str) -> Project | None:
        """按 ID 查询单个 Project。"""
        result = await self._session.execute(
            select(ProjectORM).where(ProjectORM.project_id == project_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None
