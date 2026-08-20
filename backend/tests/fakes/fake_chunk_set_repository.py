"""ChunkSet Repository 的内存假实现。"""

from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.domain.chunk import ChunkSet


class FakeChunkSetRepository(ChunkSetRepository):
    """不依赖数据库的 ChunkSet Repository 假实现。"""

    def __init__(self) -> None:
        self._chunk_sets: dict[str, ChunkSet] = {}

    async def add(self, chunk_set: ChunkSet) -> ChunkSet:
        """将 ChunkSet 存入内存。"""
        self._chunk_sets[chunk_set.chunk_set_id] = chunk_set
        return chunk_set

    async def get_by_id(self, chunk_set_id: str) -> ChunkSet | None:
        """按 ID 返回 ChunkSet。"""
        return self._chunk_sets.get(chunk_set_id)

    async def get_by_revision_and_profile(
        self,
        parse_revision_id: str,
        profile_hash: str,
    ) -> ChunkSet | None:
        """按 Revision 和 profile 哈希返回 ChunkSet。"""
        for chunk_set in self._chunk_sets.values():
            if (
                chunk_set.parse_revision_id == parse_revision_id
                and chunk_set.profile_hash == profile_hash
            ):
                return chunk_set
        return None

    async def save(self, chunk_set: ChunkSet) -> None:
        """保存 ChunkSet 状态更新。"""
        self._chunk_sets[chunk_set.chunk_set_id] = chunk_set
