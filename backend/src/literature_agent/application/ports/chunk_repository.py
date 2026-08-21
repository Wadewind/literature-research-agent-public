"""Chunk Repository 端口。"""

from typing import Protocol

from literature_agent.domain.chunk import Chunk, ChunkElementLink


class ChunkRepository(Protocol):
    """Chunk 与 ChunkElementLink 持久化的抽象端口。"""

    async def add_many(self, chunks: list[Chunk]) -> None:
        """批量保存 Chunk。"""
        ...

    async def add_links(self, links: list[ChunkElementLink]) -> None:
        """批量保存 Chunk 到 Element 的映射。"""
        ...

    async def list_by_chunk_set(self, chunk_set_id: str) -> list[Chunk]:
        """按 ChunkSet 查询 Chunk，按 ``sequence`` 升序返回。"""
        ...

    async def list_links(self, chunk_ids: list[str]) -> list[ChunkElementLink]:
        """按 Chunk ID 列表查询 Element 映射，按 (chunk_id, sequence) 升序返回。"""
        ...

    async def count_by_chunk_set(self, chunk_set_id: str) -> int:
        """统计 ChunkSet 下的 Chunk 数量。"""
        ...

    async def list_pending_embedding(self, chunk_set_id: str, limit: int) -> list[Chunk]:
        """查询尚未生成向量的 Chunk（embedding 为 null），按 ``sequence`` 升序。"""
        ...

    async def save_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        """批量写回向量（键为 chunk_id），每批在一个短事务中提交。"""
        ...

    async def count_embedded(self, chunk_set_id: str) -> int:
        """统计 ChunkSet 下已生成向量（embedding 非 null）的 Chunk 数量。"""
        ...
