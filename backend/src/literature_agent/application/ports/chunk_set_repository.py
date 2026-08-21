"""ChunkSet Repository 端口。"""

from typing import Protocol

from literature_agent.domain.chunk import ChunkSet


class ChunkSetRepository(Protocol):
    """ChunkSet 持久化的抽象端口。"""

    async def add(self, chunk_set: ChunkSet) -> ChunkSet:
        """保存 ChunkSet。"""
        ...

    async def get_by_id(self, chunk_set_id: str) -> ChunkSet | None:
        """按 ID 查询；不存在返回 None。"""
        ...

    async def get_by_revision_and_profile(
        self,
        parse_revision_id: str,
        profile_hash: str,
    ) -> ChunkSet | None:
        """按 Parse Revision 和 profile 哈希查询（每个组合至多一条）。"""
        ...

    async def get_latest_by_revision(self, parse_revision_id: str) -> ChunkSet | None:
        """按 Parse Revision 查询最新创建的 ChunkSet；不存在返回 None。"""
        ...

    async def save(self, chunk_set: ChunkSet) -> None:
        """保存 ChunkSet 状态更新（就绪/失败/重置收尾）。"""
        ...
