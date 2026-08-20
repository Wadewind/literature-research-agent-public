"""Project 应用服务。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    ProjectArchivedError,
    ProjectHasActiveRunsError,
    ProjectNotFoundError,
)
from literature_agent.domain.project import Project, create_project

TSession = TypeVar("TSession", bound=Session)


class ProjectService:
    """Project 用例层，负责授权与事务编排。

    服务通过 ``session_factory`` 控制事务边界，通过 ``repo_factory``
    注入具体的 Repository 实现，从而与 FastAPI/SQLAlchemy 解耦。
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        repo_factory: Callable[[TSession], ProjectRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
    ) -> None:
        """初始化 ProjectService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            repo_factory: 根据 session 创建 ProjectRepository 的工厂。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂，
                用于归档前的非终态 Run 检查。
        """
        self._session_factory = session_factory
        self._repo_factory = repo_factory
        self._run_repo_factory = run_repo_factory

    async def create_project(
        self,
        actor: ActorContext,
        name: str,
        description: str,
    ) -> Project:
        """为当前 actor 创建 Project。

        参数:
            actor: 当前请求的可信用户上下文。
            name: 项目名称。
            description: 项目说明。

        返回:
            新创建的 Project。
        """
        project = create_project(actor.owner_id, name, description)
        async with self._session_factory() as session:
            repo = self._repo_factory(session)
            await repo.add(project)
            await session.commit()
        return project

    async def list_projects(
        self,
        actor: ActorContext,
        include_archived: bool = False,
    ) -> list[Project]:
        """列出当前 actor 拥有的 Project；默认排除已归档。"""
        async with self._session_factory() as session:
            repo = self._repo_factory(session)
            return await repo.list_by_owner(actor.owner_id, include_archived)

    async def get_project(self, actor: ActorContext, project_id: str) -> Project:
        """获取当前 actor 可见的单个 Project。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            repo = self._repo_factory(session)
            project = await repo.get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            return project

    async def update_project(
        self,
        actor: ActorContext,
        project_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        """修改 Project 的名称与说明。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
            ProjectArchivedError: Project 已归档，拒绝修改。
            ValueError: 两个字段都缺失，或名称违反校验规则。
        """
        async with self._session_factory() as session:
            repo = self._repo_factory(session)
            project = await repo.get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            if project.is_archived:
                raise ProjectArchivedError(project_id)
            updated = project.update_details(name=name, description=description)
            await repo.update(updated)
            await session.commit()
            return updated

    async def archive_project(self, actor: ActorContext, project_id: str) -> Project:
        """归档 Project；幂等，已归档时直接返回。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
            ProjectHasActiveRunsError: 存在非终态 Run，需先等待或取消。
        """
        async with self._session_factory() as session:
            repo = self._repo_factory(session)
            project = await repo.get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            if project.is_archived:
                return project
            run_repo = self._run_repo_factory(session)
            if await run_repo.has_active_runs(project_id):
                raise ProjectHasActiveRunsError(project_id)
            archived = project.archive()
            await repo.update(archived)
            await session.commit()
            return archived

    async def restore_project(self, actor: ActorContext, project_id: str) -> Project:
        """恢复已归档 Project；幂等，未归档时直接返回。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            repo = self._repo_factory(session)
            project = await repo.get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            if not project.is_archived:
                return project
            restored = project.restore()
            await repo.update(restored)
            await session.commit()
            return restored
