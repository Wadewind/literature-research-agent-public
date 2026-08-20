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

    async def list_by_owner(self, owner_id: str) -> list[Paper]:
        """列出 owner 个人文献库中的 Paper。"""
        ...
