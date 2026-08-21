"""EvidenceService 应用服务测试（切片 7）。

覆盖 Evidence 固化逻辑：字段 denormalize、excerpt 截断、幂等重复
提交、Run 版本范围快照校验（快照外拒绝）、空结果与顺序保持。
"""

from dataclasses import replace

import pytest

from literature_agent.application.evidence_service import EvidenceService
from literature_agent.application.retriever import RetrievalResult
from literature_agent.domain.chunk import Chunk, create_chunk_set
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.evidence import EVIDENCE_EXCERPT_MAX_CHARS
from literature_agent.domain.exceptions import EvidenceScopeError
from literature_agent.domain.run import Run, RunType, create_run
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_evidence_repository import FakeEvidenceRepository
from tests.fakes.fake_project_repository import fake_session

_PROFILE = ChunkProfile(
    embedding_provider="test", embedding_model="m", embedding_dimensions=1024
)
_REVISION_ID = "rev-1"
_SNAPSHOT = [
    {"paper_id": "paper-1", "version_id": "version-1"},
    {"paper_id": "paper-2", "version_id": "version-2"},
]


def _make_run() -> Run:
    """创建带版本范围快照的 rag_answer Run。"""
    return create_run(
        project_id="proj-1",
        owner_id="user-1",
        run_type=RunType.RAG_ANSWER,
        input_payload={"version_scope": list(_SNAPSHOT)},
    )


def _result(
    chunk_id: str,
    paper_id: str,
    version_id: str,
    *,
    text: str | None = None,
    rank: int = 1,
) -> RetrievalResult:
    """构造一条检索结果（chunk 属于共享的测试 ChunkSet）。"""
    chunk = Chunk(
        chunk_id=chunk_id,
        chunk_set_id="cs-1",
        sequence=1,
        text=text if text is not None else f"text of {chunk_id}",
        token_count=100,
        section_path="1 Intro",
        page_start=1,
        page_end=2,
        content_hash=f"hash-{chunk_id}",
    )
    return RetrievalResult(
        chunk=chunk,
        paper_id=paper_id,
        version_id=version_id,
        semantic_rank=rank,
        fts_rank=None,
        rrf_score=0.5,
        rank=rank,
    )


def _make_service(
    evidence_repo: FakeEvidenceRepository,
    chunk_set_repo: FakeChunkSetRepository,
) -> EvidenceService:
    """构造使用 Fake Repository 的 EvidenceService。"""
    return EvidenceService(
        session_factory=fake_session,
        evidence_repo_factory=lambda _session: evidence_repo,
        chunk_set_repo_factory=lambda _session: chunk_set_repo,
    )


@pytest.fixture
async def chunk_set_repo() -> FakeChunkSetRepository:
    """准备 ID 为 cs-1、映射到 rev-1 的测试 ChunkSet。"""
    repo = FakeChunkSetRepository()
    chunk_set = create_chunk_set(_REVISION_ID, _PROFILE.profile_hash)
    await repo.add(replace(chunk_set, chunk_set_id="cs-1"))
    return repo


async def test_commit_evidence_denormalizes_fields(
    chunk_set_repo: FakeChunkSetRepository,
) -> None:
    """固化：paper/version/revision/章节/页码/摘录全部落 Evidence，顺序保持。"""
    evidence_repo = FakeEvidenceRepository()
    service = _make_service(evidence_repo, chunk_set_repo)
    run = _make_run()

    evidence = await service.commit_evidence(
        run=run,
        retrieval_results=[
            _result("c1", "paper-1", "version-1", rank=1),
            _result("c2", "paper-2", "version-2", rank=2),
        ],
    )

    assert len(evidence) == 2
    first = evidence[0]
    assert first.run_id == run.run_id
    assert first.project_id == run.project_id
    assert first.chunk_id == "c1"
    assert first.paper_id == "paper-1"
    assert first.version_id == "version-1"
    assert first.parse_revision_id == _REVISION_ID
    assert first.section_path == "1 Intro"
    assert (first.page_start, first.page_end) == (1, 2)
    assert first.excerpt == "text of c1"
    assert evidence[1].chunk_id == "c2"
    # 已持久化到 Repository
    stored = await evidence_repo.list_by_run(run.run_id)
    assert [e.chunk_id for e in stored] == ["c1", "c2"]


async def test_excerpt_truncated_at_limit(
    chunk_set_repo: FakeChunkSetRepository,
) -> None:
    """excerpt 截断到 500 字符上限。"""
    evidence_repo = FakeEvidenceRepository()
    service = _make_service(evidence_repo, chunk_set_repo)
    long_text = "x" * (EVIDENCE_EXCERPT_MAX_CHARS + 100)

    evidence = await service.commit_evidence(
        run=_make_run(),
        retrieval_results=[_result("c1", "paper-1", "version-1", text=long_text)],
    )

    assert len(evidence[0].excerpt) == EVIDENCE_EXCERPT_MAX_CHARS
    assert evidence[0].excerpt == long_text[:EVIDENCE_EXCERPT_MAX_CHARS]


async def test_repeated_commit_is_idempotent(
    chunk_set_repo: FakeChunkSetRepository,
) -> None:
    """重复提交幂等：已存在的 (run_id, chunk_id) 回读返回，不产生重复行。"""
    evidence_repo = FakeEvidenceRepository()
    service = _make_service(evidence_repo, chunk_set_repo)
    run = _make_run()
    results = [_result("c1", "paper-1", "version-1")]

    first = await service.commit_evidence(run=run, retrieval_results=results)
    second = await service.commit_evidence(run=run, retrieval_results=results)

    assert second[0].evidence_id == first[0].evidence_id
    assert len(await evidence_repo.list_by_run(run.run_id)) == 1


async def test_version_outside_snapshot_rejected(
    chunk_set_repo: FakeChunkSetRepository,
) -> None:
    """检索结果的 version 不在 Run 快照内：拒绝固化且不写入任何 Evidence。"""
    evidence_repo = FakeEvidenceRepository()
    service = _make_service(evidence_repo, chunk_set_repo)
    run = _make_run()

    with pytest.raises(EvidenceScopeError):
        await service.commit_evidence(
            run=run,
            retrieval_results=[_result("c1", "paper-1", "version-other")],
        )

    assert await evidence_repo.list_by_run(run.run_id) == []


async def test_paper_version_pair_mismatch_rejected(
    chunk_set_repo: FakeChunkSetRepository,
) -> None:
    """version 在快照内但 paper 配对不符（跨论文换版）：同样拒绝。"""
    evidence_repo = FakeEvidenceRepository()
    service = _make_service(evidence_repo, chunk_set_repo)

    with pytest.raises(EvidenceScopeError):
        await service.commit_evidence(
            run=_make_run(),
            retrieval_results=[_result("c1", "paper-2", "version-1")],
        )


async def test_missing_snapshot_rejected(
    chunk_set_repo: FakeChunkSetRepository,
) -> None:
    """Run input_payload 缺少版本范围快照：拒绝固化。"""
    evidence_repo = FakeEvidenceRepository()
    service = _make_service(evidence_repo, chunk_set_repo)
    run = create_run(
        project_id="proj-1", owner_id="user-1", run_type=RunType.RAG_ANSWER
    )

    with pytest.raises(EvidenceScopeError):
        await service.commit_evidence(
            run=run, retrieval_results=[_result("c1", "paper-1", "version-1")]
        )


async def test_empty_results_commit_nothing(
    chunk_set_repo: FakeChunkSetRepository,
) -> None:
    """空检索结果合法：返回空列表，不写入。"""
    evidence_repo = FakeEvidenceRepository()
    service = _make_service(evidence_repo, chunk_set_repo)
    run = _make_run()

    evidence = await service.commit_evidence(run=run, retrieval_results=[])

    assert evidence == []
    assert await evidence_repo.list_by_run(run.run_id) == []
