"""Project Repository 端口。"""

from typing import Protocol

from literature_agent.domain.project import Project


class ProjectRepository(Protocol):
    """Project 持久化的抽象端口。

    具体实现可以是 PostgreSQL、内存 Fake 或其他存储。
    """

    async def add(self, project: Project) -> Project:
        """保存 Project。"""
        ...

    async def list_by_owner(self, owner_id: str) -> list[Project]:
        """按所有者列出所有 Project。"""
        ...

    async def get_by_id(self, project_id: str) -> Project | None:
        """按 ID 查询单个 Project；不存在返回 None。"""
        ...
