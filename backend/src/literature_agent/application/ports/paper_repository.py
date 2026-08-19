"""Paper Repository 端口。"""

from typing import Protocol

from literature_agent.domain.paper import Paper


class PaperRepository(Protocol):
    """Paper 持久化的抽象端口。"""

    async def add(self, paper: Paper) -> Paper:
        """保存 Paper。"""
        ...

    async def get_by_id(self, paper_id: str) -> Paper | None:
        """按 ID 查询 Paper；不存在返回 None。"""
        ...

    async def list_by_project(self, project_id: str) -> list[Paper]:
        """按 Project ID 列出所有 Paper。"""
        ...
