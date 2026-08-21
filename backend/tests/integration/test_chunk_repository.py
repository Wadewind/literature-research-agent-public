"""ChunkSet / Chunk / ChunkElementLink Repository 的 PostgreSQL 集成测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from literature_agent.domain.chunk import (
    Chunk,
    ChunkElementLink,
    ChunkSetStatus,
    create_chunk_set,
)
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementType,
    compute_content_hash,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.element_repository import (
    SqlalchemyElementRepository,
)
from literature_agent.infrastructure.persistence.models import ChunkORM
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.parse_revision_repository import (
    SqlalchemyParseRevisionRepository,
)

_PARSE_PROFILE = ParseProfile("fake", "1.0", {})
_CHUNK_PROFILE = ChunkProfile(
    embedding_provider="test", embedding_model="m", embedding_dimensions=1024
)


@pytest_asyncio.fixture
async def revision_id(session, project: str) -> str:
    """创建 Paper/Version/Parse Revision，返回 revision_id。"""
    paper = create_paper(owner_id="user-1")
    await SqlalchemyPaperRepository(session).add(paper)
    await session.flush()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="c" * 64,
        storage_key="user-1/proj/paper/paper.pdf",
        size_bytes=100,
        content_type="application/pdf",
    )
    await SqlalchemyPaperVersionRepository(session).add(version)
    await session.flush()
    revision = create_parse_revision(
        version.version_id,
        _PARSE_PROFILE.parser_name,
        _PARSE_PROFILE.parser_version,
        _PARSE_PROFILE.profile_hash,
    )
    await SqlalchemyParseRevisionRepository(session).add(revision)
    await session.commit()
    return revision.revision_id


@pytest_asyncio.fixture
async def chunk_set_id(session, revision_id: str) -> str:
    """创建一个 RUNNING 的 ChunkSet，返回 chunk_set_id。"""
    chunk_set = create_chunk_set(
        revision_id, _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    )
    await SqlalchemyChunkSetRepository(session).add(chunk_set)
    await session.commit()
    return chunk_set.chunk_set_id


def _chunk(chunk_set_id: str, sequence: int, text: str | None = None) -> Chunk:
    """构造测试 Chunk。"""
    text = text or f"块{sequence}"
    return Chunk(
        chunk_id=str(uuid4()),
        chunk_set_id=chunk_set_id,
        sequence=sequence,
        text=text,
        token_count=10,
        section_path="1",
        page_start=1,
        page_end=2,
        content_hash=compute_content_hash("paragraph", text, {}),
    )


def _element(revision_id: str, sequence: int) -> DocumentElement:
    """构造测试 Element。"""
    text = f"段落{sequence}"
    return DocumentElement(
        element_id=str(uuid4()),
        revision_id=revision_id,
        element_type=ElementType.PARAGRAPH,
        sequence=sequence,
        text=text,
        content_hash=compute_content_hash("paragraph", text, {}),
    )


async def test_chunk_set_roundtrip_and_unique(session, revision_id: str) -> None:
    """ChunkSet 可写入读回；同 revision + profile 唯一。"""
    repo = SqlalchemyChunkSetRepository(session)
    chunk_set = create_chunk_set(
        revision_id, _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    )
    await repo.add(chunk_set)
    await session.commit()

    loaded = await repo.get_by_revision_and_profile(
        revision_id, _CHUNK_PROFILE.profile_hash
    )
    assert loaded is not None
    assert loaded.chunk_set_id == chunk_set.chunk_set_id
    assert loaded.status == ChunkSetStatus.RUNNING
    assert loaded.config == _CHUNK_PROFILE.config

    duplicate = create_chunk_set(
        revision_id, _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    )
    await repo.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_chunk_set_save_status(session, chunk_set_id: str) -> None:
    """save 应持久化状态、错误与完成时间。"""
    repo = SqlalchemyChunkSetRepository(session)
    chunk_set = await repo.get_by_id(chunk_set_id)
    assert chunk_set is not None
    now = datetime.now(UTC)
    await repo.save(chunk_set.mark_ready(now))
    await session.commit()

    loaded = await repo.get_by_id(chunk_set_id)
    assert loaded is not None
    assert loaded.status == ChunkSetStatus.READY
    assert loaded.completed_at is not None

    failed = loaded.mark_failed({"type": "RuntimeError", "message": "x"}, now)
    await repo.save(failed)
    await session.commit()
    loaded = await repo.get_by_id(chunk_set_id)
    assert loaded is not None
    assert loaded.status == ChunkSetStatus.FAILED
    assert loaded.error is not None
    assert loaded.error["type"] == "RuntimeError"


async def test_chunks_roundtrip_and_sequence_unique(session, chunk_set_id: str) -> None:
    """Chunk 批量写入、顺序读取、字段往返与 (chunk_set, sequence) 唯一约束。"""
    repo = SqlalchemyChunkRepository(session)
    chunks = [_chunk(chunk_set_id, 1), _chunk(chunk_set_id, 2)]
    await repo.add_many(chunks)
    await session.commit()

    loaded = await repo.list_by_chunk_set(chunk_set_id)
    assert [c.sequence for c in loaded] == [1, 2]
    assert loaded[0].section_path == "1"
    assert loaded[0].page_start == 1
    assert loaded[0].page_end == 2
    assert loaded[0].token_count == 10
    assert await repo.count_by_chunk_set(chunk_set_id) == 2

    await repo.add_many([_chunk(chunk_set_id, 1)])
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_chunk_element_links_roundtrip_and_pk(
    session, revision_id: str, chunk_set_id: str
) -> None:
    """映射写入读回（按 chunk/sequence 有序）与 (chunk_id, element_id) 复合主键。"""
    element_repo = SqlalchemyElementRepository(session)
    elements = [_element(revision_id, 1), _element(revision_id, 2)]
    await element_repo.add_many(elements)
    chunk_repo = SqlalchemyChunkRepository(session)
    chunk = _chunk(chunk_set_id, 1)
    await chunk_repo.add_many([chunk])
    # 先落 Chunk 再落引用它的 links（UOW 不自动推导跨 Repository 插入顺序）
    await session.flush()
    await chunk_repo.add_links(
        [
            ChunkElementLink(
                chunk_id=chunk.chunk_id, element_id=elements[1].element_id, sequence=2
            ),
            ChunkElementLink(
                chunk_id=chunk.chunk_id, element_id=elements[0].element_id, sequence=1
            ),
        ]
    )
    await session.commit()

    links = await chunk_repo.list_links([chunk.chunk_id])
    assert [(link.element_id, link.sequence) for link in links] == [
        (elements[0].element_id, 1),
        (elements[1].element_id, 2),
    ]

    # 复合主键：同一 Chunk 不重复绑定同一 Element
    await chunk_repo.add_links(
        [
            ChunkElementLink(
                chunk_id=chunk.chunk_id, element_id=elements[0].element_id, sequence=3
            )
        ]
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


def _vector(seed: float, *, index: int = 0) -> list[float]:
    """构造 1024 维测试向量：仅 index 维为 seed，其余为 0。"""
    vector = [0.0] * 1024
    vector[index] = seed
    return vector


async def test_embedding_roundtrip_and_pending(session, chunk_set_id: str) -> None:
    """向量写回与读回；pending 查询只返回 embedding 为 null 的 Chunk。"""
    repo = SqlalchemyChunkRepository(session)
    chunks = [_chunk(chunk_set_id, 1), _chunk(chunk_set_id, 2), _chunk(chunk_set_id, 3)]
    await repo.add_many(chunks)
    await session.commit()

    assert await repo.count_embedded(chunk_set_id) == 0
    pending = await repo.list_pending_embedding(chunk_set_id, limit=10)
    assert [c.sequence for c in pending] == [1, 2, 3]
    # 批次大小生效
    assert len(await repo.list_pending_embedding(chunk_set_id, limit=2)) == 2

    vector = _vector(0.5, index=7)
    await repo.save_embeddings({chunks[0].chunk_id: vector})
    await session.commit()

    loaded = await repo.list_by_chunk_set(chunk_set_id)
    assert loaded[0].embedding is not None
    assert loaded[0].embedding[7] == pytest.approx(0.5)
    assert len(loaded[0].embedding) == 1024
    assert loaded[1].embedding is None
    assert await repo.count_embedded(chunk_set_id) == 1
    pending = await repo.list_pending_embedding(chunk_set_id, limit=10)
    assert [c.sequence for c in pending] == [2, 3]


async def test_cosine_distance_top_k_ordering(session, chunk_set_id: str) -> None:
    """pgvector cosine 距离精确检索：按 <=> 排序得到正确 Top-K。"""
    repo = SqlalchemyChunkRepository(session)
    chunks = [_chunk(chunk_set_id, i + 1) for i in range(3)]
    await repo.add_many(chunks)
    await session.commit()
    # 查询向量 q 只含第 0 维：chunk1 完全同向（距离 0），chunk2 部分相关，chunk3 正交
    await repo.save_embeddings(
        {
            chunks[0].chunk_id: _vector(1.0, index=0),
            chunks[1].chunk_id: [0.5, 0.5] + [0.0] * 1022,
            chunks[2].chunk_id: _vector(1.0, index=1),
        }
    )
    await session.commit()

    result = await session.execute(
        select(ChunkORM)
        .where(ChunkORM.chunk_set_id == chunk_set_id)
        .order_by(ChunkORM.embedding.cosine_distance(_vector(1.0, index=0)))
        .limit(2)
    )
    ranked = result.scalars().all()
    assert [row.sequence for row in ranked] == [1, 2]


async def test_search_vector_generated_and_matches(session, chunk_set_id: str) -> None:
    """tsvector 生成列由 text 派生；plainto_tsquery 命中/不命中符合预期。"""
    repo = SqlalchemyChunkRepository(session)
    await repo.add_many(
        [
            _chunk(chunk_set_id, 1, text="Graph neural networks are powerful models"),
            _chunk(chunk_set_id, 2, text="Reinforcement learning for robot control"),
        ]
    )
    await session.commit()

    # 命中：english 配置支持词干化（networks → network）
    matched = await session.execute(
        select(ChunkORM).where(
            ChunkORM.chunk_set_id == chunk_set_id,
            ChunkORM.search_vector.bool_op("@@")(
                func.plainto_tsquery("english", "network")
            ),
        )
    )
    assert [row.sequence for row in matched.scalars().all()] == [1]

    # 不命中
    unmatched = await session.execute(
        select(ChunkORM).where(
            ChunkORM.chunk_set_id == chunk_set_id,
            ChunkORM.search_vector.bool_op("@@")(
                func.plainto_tsquery("english", "quantum")
            ),
        )
    )
    assert unmatched.scalars().all() == []
