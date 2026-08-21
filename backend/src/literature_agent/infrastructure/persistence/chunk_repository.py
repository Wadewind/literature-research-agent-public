"""Chunk Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.domain.chunk import Chunk, ChunkElementLink
from literature_agent.infrastructure.persistence.models import (
    ChunkElementLinkORM,
    ChunkORM,
)


def _chunk_to_domain(orm: ChunkORM) -> Chunk:
    """将 ORM 模型转换为领域实体。"""
    return Chunk(
        chunk_id=orm.chunk_id,
        chunk_set_id=orm.chunk_set_id,
        sequence=orm.sequence,
        text=orm.text,
        token_count=orm.token_count,
        section_path=orm.section_path,
        page_start=orm.page_start,
        page_end=orm.page_end,
        content_hash=orm.content_hash,
        # pgvector 驱动可能返回 numpy 数组，统一转为纯 float 列表
        embedding=(
            [float(value) for value in orm.embedding]
            if orm.embedding is not None
            else None
        ),
    )


def _chunk_to_orm(chunk: Chunk) -> ChunkORM:
    """将领域实体转换为 ORM 模型。"""
    return ChunkORM(
        chunk_id=chunk.chunk_id,
        chunk_set_id=chunk.chunk_set_id,
        sequence=chunk.sequence,
        text=chunk.text,
        token_count=chunk.token_count,
        section_path=chunk.section_path,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        content_hash=chunk.content_hash,
    )


class SqlalchemyChunkRepository(ChunkRepository):
    """基于 SQLAlchemy AsyncSession 的 ChunkRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add_many(self, chunks: list[Chunk]) -> None:
        """批量保存 Chunk。"""
        self._session.add_all([_chunk_to_orm(c) for c in chunks])

    async def add_links(self, links: list[ChunkElementLink]) -> None:
        """批量保存 Chunk 到 Element 的映射。"""
        self._session.add_all(
            [
                ChunkElementLinkORM(
                    chunk_id=link.chunk_id,
                    element_id=link.element_id,
                    sequence=link.sequence,
                )
                for link in links
            ]
        )

    async def list_by_chunk_set(self, chunk_set_id: str) -> list[Chunk]:
        """按 ChunkSet 查询 Chunk，按 sequence 升序返回。"""
        result = await self._session.execute(
            select(ChunkORM)
            .where(ChunkORM.chunk_set_id == chunk_set_id)
            .order_by(ChunkORM.sequence),
        )
        return [_chunk_to_domain(row) for row in result.scalars().all()]

    async def list_links(self, chunk_ids: list[str]) -> list[ChunkElementLink]:
        """按 Chunk ID 列表查询 Element 映射，按 (chunk_id, sequence) 升序。"""
        if not chunk_ids:
            return []
        result = await self._session.execute(
            select(ChunkElementLinkORM)
            .where(ChunkElementLinkORM.chunk_id.in_(chunk_ids))
            .order_by(ChunkElementLinkORM.chunk_id, ChunkElementLinkORM.sequence),
        )
        return [
            ChunkElementLink(
                chunk_id=row.chunk_id,
                element_id=row.element_id,
                sequence=row.sequence,
            )
            for row in result.scalars().all()
        ]

    async def count_by_chunk_set(self, chunk_set_id: str) -> int:
        """统计 ChunkSet 下的 Chunk 数量。"""
        result = await self._session.execute(
            select(func.count())
            .select_from(ChunkORM)
            .where(ChunkORM.chunk_set_id == chunk_set_id),
        )
        return result.scalar_one()

    async def list_pending_embedding(self, chunk_set_id: str, limit: int) -> list[Chunk]:
        """查询尚未生成向量的 Chunk，按 sequence 升序。"""
        result = await self._session.execute(
            select(ChunkORM)
            .where(
                ChunkORM.chunk_set_id == chunk_set_id,
                ChunkORM.embedding.is_(None),
            )
            .order_by(ChunkORM.sequence)
            .limit(limit),
        )
        return [_chunk_to_domain(row) for row in result.scalars().all()]

    async def save_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        """批量写回向量（键为 chunk_id）。"""
        for chunk_id, vector in embeddings.items():
            await self._session.execute(
                update(ChunkORM)
                .where(ChunkORM.chunk_id == chunk_id)
                .values(embedding=vector),
            )

    async def count_embedded(self, chunk_set_id: str) -> int:
        """统计已生成向量的 Chunk 数量。"""
        result = await self._session.execute(
            select(func.count())
            .select_from(ChunkORM)
            .where(
                ChunkORM.chunk_set_id == chunk_set_id,
                ChunkORM.embedding.is_not(None),
            ),
        )
        return result.scalar_one()
