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

    async def list_by_owner(self, owner_id: str) -> list[Project]:
        """按所有者列出所有 Project。"""
        result = await self._session.execute(
            select(ProjectORM)
            .where(ProjectORM.owner_id == owner_id)
            .order_by(ProjectORM.created_at.desc()),
        )
        return [_to_domain(row) for row in result.scalars().all()]

    async def get_by_id(self, project_id: str) -> Project | None:
        """按 ID 查询单个 Project。"""
        result = await self._session.execute(
            select(ProjectORM).where(ProjectORM.project_id == project_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None
