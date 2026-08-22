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

    async def get_ready_by_version(self, version_id: str) -> ChunkSet | None:
        """查询属于指定 PaperVersion 的最新 ready ChunkSet。"""
        ...

    async def count_ready_by_version_ids(self, version_ids: list[str]) -> int:
        """统计给定 PaperVersion 集合下 ready ChunkSet 的数量。

        提问提交时用于快速失败判断（范围内无任何 ready ChunkSet →
        ``project_not_indexed``）；空列表返回 0。
        """
        ...

    async def save(self, chunk_set: ChunkSet) -> None:
        """保存 ChunkSet 状态更新（就绪/失败/重置收尾）。"""
        ...
