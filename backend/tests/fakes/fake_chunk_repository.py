"""Chunk Repository 的内存假实现。"""

from dataclasses import replace
from typing import Any

from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.domain.chunk import Chunk, ChunkElementLink
from literature_agent.domain.retrieval import RetrievedChunk


class FakeChunkRepository(ChunkRepository):
    """不依赖数据库的 Chunk Repository 假实现。

    检索方法返回预设结果（``semantic_results``/``fts_results``），不模拟
    SQL 强过滤——过滤正确性由集成测试覆盖；调用参数记录在
    ``search_calls`` 供断言。
    """

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._links: list[ChunkElementLink] = []
        self.semantic_results: list[RetrievedChunk] = []
        self.fts_results: list[RetrievedChunk] = []
        self.search_calls: list[dict[str, Any]] = []

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

    async def list_pending_embedding(self, chunk_set_id: str, limit: int) -> list[Chunk]:
        """返回尚未生成向量的 Chunk，按 sequence 升序。"""
        result = [
            c
            for c in self._chunks.values()
            if c.chunk_set_id == chunk_set_id and c.embedding is None
        ]
        result.sort(key=lambda c: c.sequence)
        return result[:limit]

    async def save_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        """按 chunk_id 写回向量。"""
        for chunk_id, vector in embeddings.items():
            self._chunks[chunk_id] = replace(self._chunks[chunk_id], embedding=vector)

    async def count_embedded(self, chunk_set_id: str) -> int:
        """统计已生成向量的 Chunk 数量。"""
        return sum(
            1
            for c in self._chunks.values()
            if c.chunk_set_id == chunk_set_id and c.embedding is not None
        )

    async def search_semantic(
        self,
        *,
        owner_id: str,
        project_id: str,
        query_vector: list[float],
        limit: int,
        paper_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """返回预设的语义检索结果（截取前 limit 条）并记录调用参数。"""
        self.search_calls.append(
            {
                "path": "semantic",
                "owner_id": owner_id,
                "project_id": project_id,
                "limit": limit,
                "paper_ids": paper_ids,
            }
        )
        return list(self.semantic_results)[:limit]

    async def search_fulltext(
        self,
        *,
        owner_id: str,
        project_id: str,
        query: str,
        limit: int,
        paper_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """返回预设的全文检索结果（截取前 limit 条）并记录调用参数。"""
        self.search_calls.append(
            {
                "path": "fulltext",
                "owner_id": owner_id,
                "project_id": project_id,
                "query": query,
                "limit": limit,
                "paper_ids": paper_ids,
            }
        )
        return list(self.fts_results)[:limit]

    async def search_semantic_by_scope(
        self,
        *,
        owner_id: str,
        query_vector: list[float],
        limit: int,
        version_scope: list[tuple[str, str]],
    ) -> list[RetrievedChunk]:
        """返回预设的语义检索结果并记录快照范围参数。"""
        self.search_calls.append(
            {
                "path": "semantic_by_scope",
                "owner_id": owner_id,
                "limit": limit,
                "version_scope": version_scope,
            }
        )
        return list(self.semantic_results)[:limit]

    async def search_fulltext_by_scope(
        self,
        *,
        owner_id: str,
        query: str,
        limit: int,
        version_scope: list[tuple[str, str]],
    ) -> list[RetrievedChunk]:
        """返回预设的全文检索结果并记录快照范围参数。"""
        self.search_calls.append(
            {
                "path": "fulltext_by_scope",
                "owner_id": owner_id,
                "query": query,
                "limit": limit,
                "version_scope": version_scope,
            }
        )
        return list(self.fts_results)[:limit]
