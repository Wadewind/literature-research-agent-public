"""ChunkSet Repository 的内存假实现。"""

from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.parse_revision_repository import (
    ParseRevisionRepository,
)
from literature_agent.domain.chunk import ChunkSet, ChunkSetStatus


class FakeChunkSetRepository(ChunkSetRepository):
    """不依赖数据库的 ChunkSet Repository 假实现。

    ``count_ready_by_version_ids`` 需要 Revision → Version 的映射，
    通过可选注入的 ParseRevisionRepository 解析；未注入时返回 0。
    """

    def __init__(
        self,
        parse_revision_repo: ParseRevisionRepository | None = None,
    ) -> None:
        self._chunk_sets: dict[str, ChunkSet] = {}
        self._parse_revision_repo = parse_revision_repo

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

    async def get_latest_by_revision(self, parse_revision_id: str) -> ChunkSet | None:
        """按 Revision 返回最新创建的 ChunkSet。"""
        matches = [
            c for c in self._chunk_sets.values() if c.parse_revision_id == parse_revision_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda c: c.created_at)

    async def get_ready_by_version(self, version_id: str) -> ChunkSet | None:
        if self._parse_revision_repo is None:
            return None
        matches: list[ChunkSet] = []
        for chunk_set in self._chunk_sets.values():
            revision = await self._parse_revision_repo.get_by_id(chunk_set.parse_revision_id)
            if (
                revision is not None
                and revision.version_id == version_id
                and chunk_set.status == ChunkSetStatus.READY
            ):
                matches.append(chunk_set)
        return max(matches, key=lambda value: value.created_at) if matches else None

    async def count_ready_by_version_ids(self, version_ids: list[str]) -> int:
        """统计给定 Version 集合下 ready ChunkSet 的数量。"""
        if not version_ids or self._parse_revision_repo is None:
            return 0
        count = 0
        for chunk_set in self._chunk_sets.values():
            if chunk_set.status != ChunkSetStatus.READY:
                continue
            revision = await self._parse_revision_repo.get_by_id(
                chunk_set.parse_revision_id
            )
            if revision is not None and revision.version_id in version_ids:
                count += 1
        return count

    async def save(self, chunk_set: ChunkSet) -> None:
        """保存 ChunkSet 状态更新。"""
        self._chunk_sets[chunk_set.chunk_set_id] = chunk_set
