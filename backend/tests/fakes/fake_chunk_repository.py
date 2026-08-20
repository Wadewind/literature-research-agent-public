"""Chunk Repository 的内存假实现。"""

from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.domain.chunk import Chunk, ChunkElementLink


class FakeChunkRepository(ChunkRepository):
    """不依赖数据库的 Chunk Repository 假实现。"""

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._links: list[ChunkElementLink] = []

    async def add_many(self, chunks: list[Chunk]) -> None:
        """将 Chunk 批量存入内存。"""
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk

    async def add_links(self, links: list[ChunkElementLink]) -> None:
        """将 Element 映射批量存入内存。"""
        self._links.extend(links)

    async def list_by_chunk_set(self, chunk_set_id: str) -> list[Chunk]:
        """按 ChunkSet 返回 Chunk，按 sequence 升序。"""
        result = [c for c in self._chunks.values() if c.chunk_set_id == chunk_set_id]
        result.sort(key=lambda c: c.sequence)
        return result

    async def list_links(self, chunk_ids: list[str]) -> list[ChunkElementLink]:
        """按 Chunk ID 列表返回 Element 映射，按 (chunk_id, sequence) 升序。"""
        result = [link for link in self._links if link.chunk_id in chunk_ids]
        result.sort(key=lambda link: (link.chunk_id, link.sequence))
        return result

    async def count_by_chunk_set(self, chunk_set_id: str) -> int:
        """统计 ChunkSet 下的 Chunk 数量。"""
        return sum(1 for c in self._chunks.values() if c.chunk_set_id == chunk_set_id)
