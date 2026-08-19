"""Project Repository 的内存假实现，用于应用层测试。"""

from contextlib import asynccontextmanager

from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.domain.project import Project


class FakeProjectRepository(ProjectRepository):
    """不依赖数据库的 Repository 假实现。"""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}

    async def add(self, project: Project) -> Project:
        """将 Project 存入内存。"""
        self._projects[project.project_id] = project
        return project

    async def list_by_owner(self, owner_id: str) -> list[Project]:
        """返回指定所有者的 Project 列表。"""
        return [p for p in self._projects.values() if p.owner_id == owner_id]

    async def get_by_id(self, project_id: str) -> Project | None:
        """根据 ID 返回 Project。"""
        return self._projects.get(project_id)


class _FakeSession:
    """模拟具有 flush/commit 方法的异步会话。"""

    async def flush(self) -> None:
        """空刷新。"""

    async def commit(self) -> None:
        """空提交。"""


@asynccontextmanager
async def fake_session():
    """模拟 AsyncSession 的异步上下文管理器。"""
    yield _FakeSession()


def fake_repo_factory(_session: object) -> FakeProjectRepository:
    """返回共享的 FakeProjectRepository 实例。"""
    return fake_repo_factory.repo


fake_repo_factory.repo = FakeProjectRepository()
