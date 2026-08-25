"""Hybrid Retrieval 两路检索 SQL 的 PostgreSQL 集成测试（切片 6）。

验证语义/全文两路查询都在 SQL 内完成强过滤链：
owner（projects 与 paper_versions 双重校验）→ project → ProjectPaper
→ selected_version_id → ParseRevision → ready ChunkSet → chunks，
不允许越权/未收录/非 ready 的 Chunk 进入候选。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest_asyncio

from literature_agent.domain.chunk import Chunk, ChunkSetStatus, create_chunk_set
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import compute_content_hash
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.parse_revision_repository import (
    SqlalchemyParseRevisionRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)

_PARSE_PROFILE = ParseProfile("fake", "1.0", {})
_CHUNK_PROFILE = ChunkProfile(
    embedding_provider="test", embedding_model="m", embedding_dimensions=1024
)


@dataclass(frozen=True)
class _Seeded:
    """一篇已索引 Paper 的测试种子。"""

    paper_id: str
    version_id: str
    chunk_ids: list[str]


def _one_hot(index: int, value: float = 1.0) -> list[float]:
    """构造仅第 index 维非零的 1024 维向量。"""
    vector = [0.0] * 1024
    vector[index] = value
    return vector


async def _seed_indexed_paper(
    session,
    *,
    owner_id: str,
    project_id: str | None,
    texts: Sequence[str],
    vectors: Sequence[list[float]] | None = None,
    chunk_set_status: ChunkSetStatus = ChunkSetStatus.READY,
) -> _Seeded:
    """创建 Paper/Version/Revision/ChunkSet/Chunks 完整链并可选择收录进 Project。"""
    paper = create_paper(owner_id=owner_id)
    await SqlalchemyPaperRepository(session).add(paper)
    await session.flush()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id=owner_id,
        file_hash=uuid4().hex,
        storage_key=f"{owner_id}/{uuid4().hex}.pdf",
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
    ).mark_succeeded(datetime.now(UTC))
    await SqlalchemyParseRevisionRepository(session).add(revision)
    await session.flush()
    chunk_set = create_chunk_set(
        revision.revision_id, _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    )
    now = datetime.now(UTC)
    if chunk_set_status == ChunkSetStatus.READY:
        chunk_set = chunk_set.mark_ready(now)
    elif chunk_set_status == ChunkSetStatus.FAILED:
        chunk_set = chunk_set.mark_failed({"type": "RuntimeError", "message": "x"}, now)
    await SqlalchemyChunkSetRepository(session).add(chunk_set)
    await session.flush()

    chunk_repo = SqlalchemyChunkRepository(session)
    chunks = [
        Chunk(
            chunk_id=str(uuid4()),
            chunk_set_id=chunk_set.chunk_set_id,
            sequence=index + 1,
            text=text,
            token_count=10,
            section_path="1",
            page_start=1,
            page_end=1,
            content_hash=compute_content_hash("paragraph", text, {}),
        )
        for index, text in enumerate(texts)
    ]
    await chunk_repo.add_many(chunks)
    if vectors is not None:
        await session.flush()
        await chunk_repo.save_embeddings(
            {chunk.chunk_id: vector for chunk, vector in zip(chunks, vectors, strict=True)}
        )
    if project_id is not None:
        await SqlalchemyProjectPaperRepository(session).add(
            create_project_paper(project_id, paper.paper_id, version.version_id)
        )
    await session.commit()
    return _Seeded(
        paper_id=paper.paper_id,
        version_id=version.version_id,
        chunk_ids=[c.chunk_id for c in chunks],
    )


@pytest_asyncio.fixture
async def other_project(session) -> str:
    """user-2 的 Project（越权隔离对照）。"""
    project = create_project(owner_id="user-2", name="他人项目", description="")
    await SqlalchemyProjectRepository(session).add(project)
    await session.commit()
    return project.project_id


async def test_semantic_search_orders_by_cosine_and_enforces_scope(
    session, project: str, other_project: str
) -> None:
    """语义检索：cosine 距离升序 Top-K；跨 owner/跨 Project/未收录一律不出现。"""
    mine = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["alpha", "beta", "gamma"],
        vectors=[_one_hot(0, 0.5), _one_hot(0), _one_hot(1)],
    )
    # 跨 owner：与查询完全同向，也绝不能出现
    await _seed_indexed_paper(
        session,
        owner_id="user-2",
        project_id=other_project,
        texts=["foreign"],
        vectors=[_one_hot(0)],
    )
    # 同 owner 但未收录进本 Project
    await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=None,
        texts=["uncollected"],
        vectors=[_one_hot(0)],
    )
    repo = SqlalchemyChunkRepository(session)

    results = await repo.search_semantic(
        owner_id="user-1",
        project_id=project,
        query_vector=_one_hot(0),
        limit=10,
    )

    # chunk1（0.5 倍同向，距离 0）与 chunk2 同向同距离，chunk3 正交最后；
    # 同距离按 chunk_id 稳定排序，只断言集合与首尾
    assert {r.chunk.chunk_id for r in results} == set(mine.chunk_ids)
    assert results[-1].chunk.chunk_id == mine.chunk_ids[2]
    assert all(r.paper_id == mine.paper_id for r in results)
    assert all(r.version_id == mine.version_id for r in results)


async def test_semantic_search_respects_limit(session, project: str) -> None:
    """语义检索 Top-K：limit 生效。"""
    seeded = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["a", "b", "c"],
        vectors=[_one_hot(0), _one_hot(0), _one_hot(0)],
    )
    repo = SqlalchemyChunkRepository(session)
    results = await repo.search_semantic(
        owner_id="user-1", project_id=project, query_vector=_one_hot(0), limit=2
    )
    assert len(results) == 2
    assert {r.chunk.chunk_id for r in results} <= set(seeded.chunk_ids)


async def test_semantic_search_skips_null_embedding(session, project: str) -> None:
    """尚未生成向量的 Chunk 不参与语义检索。"""
    seeded = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["with-vector", "without-vector"],
        vectors=None,
    )
    repo = SqlalchemyChunkRepository(session)
    await repo.save_embeddings({seeded.chunk_ids[0]: _one_hot(0)})
    await session.commit()

    results = await repo.search_semantic(
        owner_id="user-1", project_id=project, query_vector=_one_hot(0), limit=10
    )
    assert [r.chunk.chunk_id for r in results] == [seeded.chunk_ids[0]]


async def test_fts_search_ranks_by_ts_rank_and_enforces_scope(
    session, project: str, other_project: str
) -> None:
    """全文检索：ts_rank 降序、词干化命中；越权数据不出现。"""
    mine = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=[
            "GraphWeave benchmark. GraphWeave benchmark. GraphWeave benchmark suite.",
            "The GraphWeave benchmark appears once.",
            "Reinforcement learning for quadruped robots.",
        ],
    )
    await _seed_indexed_paper(
        session,
        owner_id="user-2",
        project_id=other_project,
        texts=["GraphWeave benchmark GraphWeave benchmark GraphWeave benchmark"],
    )
    repo = SqlalchemyChunkRepository(session)

    results = await repo.search_fulltext(
        owner_id="user-1", project_id=project, query="graphweave", limit=10
    )

    # 高频命中排前；未命中的 chunk3 不出现；越权 chunk 不出现
    assert [r.chunk.chunk_id for r in results] == mine.chunk_ids[:2]
    assert results[0].chunk.chunk_id == mine.chunk_ids[0]


async def test_fts_search_respects_limit(session, project: str) -> None:
    """全文检索 Top-K：limit 生效。"""
    await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=[f"graphweave mention number {i}" for i in range(3)],
    )
    repo = SqlalchemyChunkRepository(session)
    results = await repo.search_fulltext(
        owner_id="user-1", project_id=project, query="graphweave", limit=2
    )
    assert len(results) == 2


async def test_unselected_version_is_not_searched(session, project: str) -> None:
    """同一 Paper 的未选中 Version（有 ready ChunkSet）不参与检索。"""
    paper = create_paper(owner_id="user-1")
    await SqlalchemyPaperRepository(session).add(paper)
    await session.flush()
    version_repo = SqlalchemyPaperVersionRepository(session)
    selected_version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash=uuid4().hex,
        storage_key="user-1/selected.pdf",
        size_bytes=1,
        content_type="application/pdf",
    )
    other_version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash=uuid4().hex,
        storage_key="user-1/other.pdf",
        size_bytes=1,
        content_type="application/pdf",
    )
    await version_repo.add(selected_version)
    await version_repo.add(other_version)
    await session.flush()

    chunk_ids: dict[str, list[str]] = {}
    for version in (selected_version, other_version):
        revision = create_parse_revision(
            version.version_id,
            _PARSE_PROFILE.parser_name,
            _PARSE_PROFILE.parser_version,
            _PARSE_PROFILE.profile_hash,
        ).mark_succeeded(datetime.now(UTC))
        await SqlalchemyParseRevisionRepository(session).add(revision)
        await session.flush()
        chunk_set = create_chunk_set(
            revision.revision_id, _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
        ).mark_ready(datetime.now(UTC))
        await SqlalchemyChunkSetRepository(session).add(chunk_set)
        await session.flush()
        chunk = Chunk(
            chunk_id=str(uuid4()),
            chunk_set_id=chunk_set.chunk_set_id,
            sequence=1,
            text="GraphWeave benchmark suite",
            token_count=10,
            content_hash=compute_content_hash("paragraph", "x", {}),
        )
        await SqlalchemyChunkRepository(session).add_many([chunk])
        await session.flush()
        chunk_ids[version.version_id] = [chunk.chunk_id]
    await SqlalchemyProjectPaperRepository(session).add(
        create_project_paper(project, paper.paper_id, selected_version.version_id)
    )
    await session.commit()

    repo = SqlalchemyChunkRepository(session)
    results = await repo.search_fulltext(
        owner_id="user-1", project_id=project, query="graphweave", limit=10
    )
    assert [r.chunk.chunk_id for r in results] == chunk_ids[selected_version.version_id]
    assert results[0].version_id == selected_version.version_id


async def test_non_ready_chunk_set_is_not_searched(session, project: str) -> None:
    """running/failed ChunkSet 的 Chunk 在两路检索中都不出现。"""
    for status in (ChunkSetStatus.RUNNING, ChunkSetStatus.FAILED):
        await _seed_indexed_paper(
            session,
            owner_id="user-1",
            project_id=project,
            texts=["graphweave from non-ready set"],
            vectors=[_one_hot(0)],
            chunk_set_status=status,
        )
    repo = SqlalchemyChunkRepository(session)
    assert (
        await repo.search_fulltext(
            owner_id="user-1", project_id=project, query="graphweave", limit=10
        )
        == []
    )
    assert (
        await repo.search_semantic(
            owner_id="user-1", project_id=project, query_vector=_one_hot(0), limit=10
        )
        == []
    )


async def test_selected_papers_subset_filter(session, project: str) -> None:
    """selected_papers 范围过滤在 SQL 内完成：范围外 Paper 不出现。"""
    included = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["graphweave included"],
    )
    await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["graphweave excluded"],
    )
    repo = SqlalchemyChunkRepository(session)
    results = await repo.search_fulltext(
        owner_id="user-1",
        project_id=project,
        query="graphweave",
        limit=10,
        paper_ids=[included.paper_id],
    )
    assert [r.paper_id for r in results] == [included.paper_id]
    # 空子集等价于无候选
    assert (
        await repo.search_fulltext(
            owner_id="user-1", project_id=project, query="graphweave", limit=10, paper_ids=[]
        )
        == []
    )


async def test_result_carries_chunk_fields(session, project: str) -> None:
    """检索结果携带 Chunk 检索所需字段（text/token/section/page）与归属。"""
    seeded = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["graphweave benchmark"],
        vectors=[_one_hot(0)],
    )
    repo = SqlalchemyChunkRepository(session)
    result = (
        await repo.search_fulltext(
            owner_id="user-1", project_id=project, query="graphweave", limit=1
        )
    )[0]
    assert result.chunk.chunk_id == seeded.chunk_ids[0]
    assert result.chunk.text == "graphweave benchmark"
    assert result.chunk.token_count == 10
    assert result.chunk.section_path == "1"
    assert result.chunk.page_start == 1
    assert result.chunk.embedding is not None
    assert result.paper_id == seeded.paper_id
    assert result.version_id == seeded.version_id


# ---------- 切片 8：版本范围快照检索（search_*_by_scope） ----------


async def test_scope_search_only_hits_snapshot_entries(session, project: str) -> None:
    """快照检索只返回快照内的 (paper_id, version_id)，范围外论文不命中。"""
    included = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["graphweave included"],
        vectors=[_one_hot(0)],
    )
    excluded = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["graphweave excluded"],
        vectors=[_one_hot(0)],
    )
    repo = SqlalchemyChunkRepository(session)
    scope = [(included.paper_id, included.version_id)]

    fts = await repo.search_fulltext_by_scope(
        owner_id="user-1", query="graphweave", limit=10, version_scope=scope
    )
    semantic = await repo.search_semantic_by_scope(
        owner_id="user-1", query_vector=_one_hot(0), limit=10, version_scope=scope
    )

    assert [r.chunk.chunk_id for r in fts] == included.chunk_ids
    assert {r.chunk.chunk_id for r in semantic} == set(included.chunk_ids)
    assert all(r.paper_id == included.paper_id for r in [*fts, *semantic])
    assert excluded.chunk_ids[0] not in {r.chunk.chunk_id for r in semantic}


async def test_scope_search_pins_exact_chunk_set_for_same_version(
    session, project: str
) -> None:
    """同一 Version 有两个 READY ChunkSet 时，两路 SQL 只命中冻结 ChunkSet。"""
    paper = create_paper(owner_id="user-1")
    await SqlalchemyPaperRepository(session).add(paper)
    await session.flush()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash=uuid4().hex,
        storage_key="user-1/frozen-version.pdf",
        size_bytes=1,
        content_type="application/pdf",
    )
    await SqlalchemyPaperVersionRepository(session).add(version)
    await session.flush()
    chunk_sets = []
    chunks = []
    for index in range(2):
        revision = create_parse_revision(
            version.version_id,
            "fake",
            f"{index + 1}.0",
            uuid4().hex,
        ).mark_succeeded(datetime.now(UTC))
        await SqlalchemyParseRevisionRepository(session).add(revision)
        await session.flush()
        chunk_set = create_chunk_set(
            revision.revision_id,
            uuid4().hex,
            _CHUNK_PROFILE.config,
        ).mark_ready(datetime.now(UTC))
        await SqlalchemyChunkSetRepository(session).add(chunk_set)
        await session.flush()
        chunk = Chunk(
            chunk_id=str(uuid4()),
            chunk_set_id=chunk_set.chunk_set_id,
            sequence=1,
            text=f"graphweave ready set {index}",
            token_count=10,
            content_hash=compute_content_hash("paragraph", f"set-{index}", {}),
        )
        await SqlalchemyChunkRepository(session).add_many([chunk])
        await session.flush()
        await SqlalchemyChunkRepository(session).save_embeddings(
            {chunk.chunk_id: _one_hot(0)}
        )
        chunk_sets.append(chunk_set)
        chunks.append(chunk)
    await SqlalchemyProjectPaperRepository(session).add(
        create_project_paper(project, paper.paper_id, version.version_id)
    )
    await session.commit()
    repo = SqlalchemyChunkRepository(session)
    scope = [(paper.paper_id, version.version_id)]
    frozen = [chunk_sets[0].chunk_set_id]

    fts = await repo.search_fulltext_by_scope(
        owner_id="user-1",
        query="graphweave",
        limit=10,
        version_scope=scope,
        chunk_set_scope=frozen,
    )
    semantic = await repo.search_semantic_by_scope(
        owner_id="user-1",
        query_vector=_one_hot(0),
        limit=10,
        version_scope=scope,
        chunk_set_scope=frozen,
    )

    assert [item.chunk.chunk_id for item in fts] == [chunks[0].chunk_id]
    assert [item.chunk.chunk_id for item in semantic] == [chunks[0].chunk_id]


async def test_scope_search_survives_project_paper_removal(
    session, project: str
) -> None:
    """移出 Project 后快照检索仍命中：快照语义优先于当前收录关系。"""
    seeded = await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["graphweave removed paper"],
        vectors=[_one_hot(0)],
    )
    removed = await SqlalchemyProjectPaperRepository(session).remove(
        project, seeded.paper_id
    )
    assert removed
    await session.commit()
    repo = SqlalchemyChunkRepository(session)
    scope = [(seeded.paper_id, seeded.version_id)]

    fts = await repo.search_fulltext_by_scope(
        owner_id="user-1", query="graphweave", limit=10, version_scope=scope
    )
    semantic = await repo.search_semantic_by_scope(
        owner_id="user-1", query_vector=_one_hot(0), limit=10, version_scope=scope
    )

    assert [r.chunk.chunk_id for r in fts] == seeded.chunk_ids
    assert {r.chunk.chunk_id for r in semantic} == set(seeded.chunk_ids)


async def test_scope_search_rejects_other_owner_version(
    session, project: str, other_project: str
) -> None:
    """快照指向他人 owner 的 Version：owner 过滤保留，不命中。"""
    foreign = await _seed_indexed_paper(
        session,
        owner_id="user-2",
        project_id=other_project,
        texts=["graphweave foreign"],
        vectors=[_one_hot(0)],
    )
    repo = SqlalchemyChunkRepository(session)
    scope = [(foreign.paper_id, foreign.version_id)]

    assert (
        await repo.search_fulltext_by_scope(
            owner_id="user-1", query="graphweave", limit=10, version_scope=scope
        )
        == []
    )
    assert (
        await repo.search_semantic_by_scope(
            owner_id="user-1", query_vector=_one_hot(0), limit=10, version_scope=scope
        )
        == []
    )


async def test_scope_search_skips_non_ready_chunk_set(session, project: str) -> None:
    """非 ready ChunkSet 的 Chunk 在快照检索两路中都不出现。"""
    for chunk_set_status in (ChunkSetStatus.RUNNING, ChunkSetStatus.FAILED):
        seeded = await _seed_indexed_paper(
            session,
            owner_id="user-1",
            project_id=project,
            texts=["graphweave non-ready scope"],
            vectors=[_one_hot(0)],
            chunk_set_status=chunk_set_status,
        )
        repo = SqlalchemyChunkRepository(session)
        scope = [(seeded.paper_id, seeded.version_id)]
        assert (
            await repo.search_fulltext_by_scope(
                owner_id="user-1", query="graphweave", limit=10, version_scope=scope
            )
            == []
        )
        assert (
            await repo.search_semantic_by_scope(
                owner_id="user-1", query_vector=_one_hot(0), limit=10,
                version_scope=scope,
            )
            == []
        )


async def test_scope_search_empty_snapshot_returns_empty(session, project: str) -> None:
    """空快照等价于无候选（tuple IN 空集），两路都返回空。"""
    await _seed_indexed_paper(
        session,
        owner_id="user-1",
        project_id=project,
        texts=["graphweave anything"],
        vectors=[_one_hot(0)],
    )
    repo = SqlalchemyChunkRepository(session)
    assert (
        await repo.search_fulltext_by_scope(
            owner_id="user-1", query="graphweave", limit=10, version_scope=[]
        )
        == []
    )
    assert (
        await repo.search_semantic_by_scope(
            owner_id="user-1", query_vector=_one_hot(0), limit=10, version_scope=[]
        )
        == []
    )
