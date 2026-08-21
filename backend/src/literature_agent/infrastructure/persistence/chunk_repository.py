"""Chunk Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.domain.chunk import Chunk, ChunkElementLink, ChunkSetStatus
from literature_agent.domain.retrieval import RetrievedChunk
from literature_agent.infrastructure.persistence.models import (
    ChunkElementLinkORM,
    ChunkORM,
    ChunkSetORM,
    DocumentParseRevisionORM,
    PaperVersionORM,
    ProjectORM,
    ProjectPaperORM,
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

    async def search_semantic(
        self,
        *,
        owner_id: str,
        project_id: str,
        query_vector: list[float],
        limit: int,
        paper_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Project 强过滤范围内按 cosine 距离升序的向量检索 Top-K。"""
        statement = (
            self._scoped_select(owner_id=owner_id, project_id=project_id, paper_ids=paper_ids)
            .where(ChunkORM.embedding.is_not(None))
            # 同距离时按 chunk_id 稳定排序，保证结果确定性
            .order_by(ChunkORM.embedding.cosine_distance(query_vector), ChunkORM.chunk_id)
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return [
            RetrievedChunk(chunk=_chunk_to_domain(row), paper_id=paper_id, version_id=version_id)
            for row, paper_id, version_id in result.all()
        ]

    async def search_fulltext(
        self,
        *,
        owner_id: str,
        project_id: str,
        query: str,
        limit: int,
        paper_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Project 强过滤范围内的全文检索 Top-K（english 配置，ts_rank 降序）。"""
        ts_query = func.plainto_tsquery("english", query)
        statement = (
            self._scoped_select(owner_id=owner_id, project_id=project_id, paper_ids=paper_ids)
            .where(ChunkORM.search_vector.bool_op("@@")(ts_query))
            .order_by(func.ts_rank(ChunkORM.search_vector, ts_query).desc(), ChunkORM.chunk_id)
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return [
            RetrievedChunk(chunk=_chunk_to_domain(row), paper_id=paper_id, version_id=version_id)
            for row, paper_id, version_id in result.all()
        ]

    @staticmethod
    def _scoped_select(
        *,
        owner_id: str,
        project_id: str,
        paper_ids: list[str] | None,
    ):
        """构造带强过滤链的 Chunk 查询（不含排序与 limit）。

        过滤链全部在 SQL 内完成：projects.owner_id/project_id →
        ProjectPaper（paper 收录且 selected_version_id 指向该 Version）→
        paper_versions.owner_id（双重校验）→ ParseRevision → ready
        ChunkSet → chunks；``paper_ids`` 非 None 时限制到 Paper 子集。
        """
        statement = (
            select(ChunkORM, ProjectPaperORM.paper_id, PaperVersionORM.version_id)
            .join(ChunkSetORM, ChunkORM.chunk_set_id == ChunkSetORM.chunk_set_id)
            .join(
                DocumentParseRevisionORM,
                ChunkSetORM.parse_revision_id == DocumentParseRevisionORM.revision_id,
            )
            .join(
                PaperVersionORM,
                DocumentParseRevisionORM.version_id == PaperVersionORM.version_id,
            )
            .join(
                ProjectPaperORM,
                and_(
                    ProjectPaperORM.paper_id == PaperVersionORM.paper_id,
                    ProjectPaperORM.selected_version_id == PaperVersionORM.version_id,
                ),
            )
            .join(ProjectORM, ProjectPaperORM.project_id == ProjectORM.project_id)
            .where(
                ProjectORM.project_id == project_id,
                ProjectORM.owner_id == owner_id,
                PaperVersionORM.owner_id == owner_id,
                ChunkSetORM.status == ChunkSetStatus.READY.value,
            )
        )
        if paper_ids is not None:
            statement = statement.where(ProjectPaperORM.paper_id.in_(paper_ids))
        return statement
