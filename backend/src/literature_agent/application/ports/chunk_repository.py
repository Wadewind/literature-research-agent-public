"""Chunk Repository 端口。"""

from typing import Protocol

from literature_agent.domain.chunk import Chunk, ChunkElementLink
from literature_agent.domain.retrieval import RetrievedChunk


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

    async def search_semantic(
        self,
        *,
        owner_id: str,
        project_id: str,
        query_vector: list[float],
        limit: int,
        paper_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Project 范围内按 cosine 距离升序的向量检索 Top-K。

        强过滤链必须在 SQL 内完成：owner（projects 与 paper_versions
        双重校验）→ project → ProjectPaper → selected_version_id →
        ParseRevision → ready ChunkSet → chunks；``paper_ids`` 非 None
        时进一步限制到该 Paper 子集（selected_papers 范围）。
        embedding 为 null 的 Chunk 不参与。不允许先取全量再应用层过滤。
        """
        ...

    async def search_fulltext(
        self,
        *,
        owner_id: str,
        project_id: str,
        query: str,
        limit: int,
        paper_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Project 范围内的 PostgreSQL 全文检索 Top-K（english 配置）。

        使用 ``search_vector @@ plainto_tsquery('english', query)`` 命中
        并按 ``ts_rank`` 降序；强过滤链与 ``search_semantic`` 相同。
        """
        ...

    async def search_semantic_by_scope(
        self,
        *,
        owner_id: str,
        query_vector: list[float],
        limit: int,
        version_scope: list[tuple[str, str]],
    ) -> list[RetrievedChunk]:
        """按 Run 固化的版本范围快照做向量检索 Top-K（cosine 距离升序）。

        与 ``search_semantic`` 的差别：不 join ``project_papers`` 当前
        收录关系，只按显式 ``(paper_id, version_id)`` 快照集合过滤——
        Paper 被移出 Project 后，本次 Run 仍按快照检索完（快照语义
        优先）。owner 校验保留（paper_versions.owner_id），ChunkSet
        仍需 ready。空快照直接返回空列表。
        """
        ...

    async def search_fulltext_by_scope(
        self,
        *,
        owner_id: str,
        query: str,
        limit: int,
        version_scope: list[tuple[str, str]],
    ) -> list[RetrievedChunk]:
        """按版本范围快照的全文检索 Top-K（english 配置，ts_rank 降序）。

        过滤语义与 ``search_semantic_by_scope`` 相同。
        """
        ...
