"""Evidence / ClaimSet / Claim / Citation 持久化的 PostgreSQL 集成测试（切片 7）。

覆盖四张表的字段往返、唯一约束（(run_id, chunk_id)、claim_sets.run_id、
(claim_set_id, sequence)、citations 复合主键）、FK 约束行为与跨 Run
Evidence 隔离查询。
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from literature_agent.domain.chunk import Chunk, create_chunk_set
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import compute_content_hash
from literature_agent.domain.evidence import (
    AnswerStatus,
    Citation,
    create_claim,
    create_claim_set,
    create_evidence,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.run import Run, RunType, create_run
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
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
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)

_PARSE_PROFILE = ParseProfile("fake", "1.0", {})
_CHUNK_PROFILE = ChunkProfile(
    embedding_provider="test", embedding_model="m", embedding_dimensions=1024
)


def _rag_run(project_id: str) -> Run:
    """创建带版本范围快照的 rag_answer Run。"""
    return create_run(
        project_id=project_id,
        owner_id="user-1",
        run_type=RunType.RAG_ANSWER,
        input_payload={"version_scope": [{"paper_id": "p", "version_id": "v"}]},
    )


@pytest_asyncio.fixture
async def chunk_id(session, project: str) -> str:
    """创建 Paper/Version/Revision/ChunkSet/Chunk 链，返回 chunk_id。"""
    paper = create_paper(owner_id="user-1")
    await SqlalchemyPaperRepository(session).add(paper)
    await session.flush()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="d" * 64,
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
    await session.flush()
    chunk_set = create_chunk_set(
        revision.revision_id, _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    )
    await SqlalchemyChunkSetRepository(session).add(chunk_set)
    await session.flush()
    text = "Graph neural networks for molecules"
    chunk = Chunk(
        chunk_id=str(uuid4()),
        chunk_set_id=chunk_set.chunk_set_id,
        sequence=1,
        text=text,
        token_count=10,
        section_path="1 Intro",
        page_start=1,
        page_end=2,
        content_hash=compute_content_hash("paragraph", text, {}),
    )
    await SqlalchemyChunkRepository(session).add_many([chunk])
    await session.commit()
    return chunk.chunk_id


@pytest_asyncio.fixture
async def run_id(session, project: str) -> str:
    """创建一个 rag_answer Run 并返回其 ID。"""
    run = _rag_run(project)
    await SqlalchemyRunRepository(session).add(run)
    await session.commit()
    return run.run_id


def _evidence(run_id: str, chunk_id: str):
    """构造一条测试 Evidence。"""
    return create_evidence(
        run_id=run_id,
        project_id="proj",
        paper_id="paper-1",
        version_id="version-1",
        parse_revision_id="rev-1",
        chunk_id=chunk_id,
        section_path="1 Intro",
        page_start=1,
        page_end=2,
        excerpt="摘录文本",
    )


async def test_evidence_roundtrip_and_run_chunk_unique(
    session, run_id: str, chunk_id: str
) -> None:
    """Evidence 写入读回字段完整；同一 Run 内同一 Chunk 只固化一条。"""
    repo = SqlalchemyEvidenceRepository(session)
    evidence = _evidence(run_id, chunk_id)
    await repo.add_many([evidence])
    await session.commit()

    loaded = await repo.list_by_run(run_id)
    assert len(loaded) == 1
    assert loaded[0].evidence_id == evidence.evidence_id
    assert loaded[0].chunk_id == chunk_id
    assert loaded[0].section_path == "1 Intro"
    assert (loaded[0].page_start, loaded[0].page_end) == (1, 2)
    assert loaded[0].excerpt == "摘录文本"

    # 唯一约束 (run_id, chunk_id)：重复固化被拒绝
    await repo.add_many([_evidence(run_id, chunk_id)])
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_list_by_run_isolates_across_runs(
    session, project: str, chunk_id: str
) -> None:
    """list_by_run 只返回本 Run 的 Evidence，跨 Run 隔离。"""
    run_a, run_b = _rag_run(project), _rag_run(project)
    run_repo = SqlalchemyRunRepository(session)
    await run_repo.add(run_a)
    await run_repo.add(run_b)
    # 先落 Run 再落引用它的 Evidence（UOW 不自动推导跨 Repository 插入顺序）
    await session.flush()
    # 同一 Chunk 可在不同 Run 各固化一条（唯一约束只限制 Run 内）
    repo = SqlalchemyEvidenceRepository(session)
    await repo.add_many([_evidence(run_a.run_id, chunk_id)])
    await repo.add_many([_evidence(run_b.run_id, chunk_id)])
    await session.commit()

    loaded_a = await repo.list_by_run(run_a.run_id)
    loaded_b = await repo.list_by_run(run_b.run_id)
    assert len(loaded_a) == 1
    assert len(loaded_b) == 1
    assert loaded_a[0].run_id == run_a.run_id
    assert loaded_b[0].run_id == run_b.run_id

    by_ids = await repo.list_by_ids(
        [loaded_a[0].evidence_id, loaded_b[0].evidence_id, str(uuid4())]
    )
    assert {e.run_id for e in by_ids} == {run_a.run_id, run_b.run_id}


async def test_claim_set_roundtrip_and_run_unique(session, run_id: str) -> None:
    """ClaimSet 写入读回；一个 Run 只允许一个 ClaimSet。"""
    repo = SqlalchemyClaimSetRepository(session)
    claim_set = create_claim_set(run_id, AnswerStatus.ANSWERED)
    await repo.add_claim_set(claim_set)
    await session.commit()

    loaded = await repo.get_by_run_id(run_id)
    assert loaded is not None
    assert loaded.claim_set_id == claim_set.claim_set_id
    assert loaded.answer_status is AnswerStatus.ANSWERED

    # claim_sets.run_id 唯一：同一 Run 第二个 ClaimSet 被拒绝
    await repo.add_claim_set(create_claim_set(run_id, AnswerStatus.ANSWERED))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_claims_roundtrip_and_sequence_unique(session, run_id: str) -> None:
    """Claim 按 sequence 有序读回；(claim_set_id, sequence) 唯一。"""
    repo = SqlalchemyClaimSetRepository(session)
    claim_set = create_claim_set(run_id, AnswerStatus.ANSWERED)
    await repo.add_claim_set(claim_set)
    await session.flush()
    await repo.add_claims(
        [
            create_claim(claim_set.claim_set_id, 1, "论述一"),
            create_claim(claim_set.claim_set_id, 2, "论述二"),
        ]
    )
    await session.commit()

    claims = await repo.list_claims(claim_set.claim_set_id)
    assert [(c.sequence, c.text) for c in claims] == [(1, "论述一"), (2, "论述二")]

    await repo.add_claims([create_claim(claim_set.claim_set_id, 1, "重复序号")])
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_citations_composite_pk_and_fk(
    session, run_id: str, chunk_id: str
) -> None:
    """Citation 复合主键去重；引用不存在的 Claim/Evidence 被 FK 拒绝。"""
    repo = SqlalchemyClaimSetRepository(session)
    evidence_repo = SqlalchemyEvidenceRepository(session)
    evidence = _evidence(run_id, chunk_id)
    await evidence_repo.add_many([evidence])
    claim_set = create_claim_set(run_id, AnswerStatus.ANSWERED)
    await repo.add_claim_set(claim_set)
    await session.flush()
    claim = create_claim(claim_set.claim_set_id, 1, "论述")
    await repo.add_claims([claim])
    await session.flush()
    await repo.add_citations([Citation(claim_id=claim.claim_id, evidence_id=evidence.evidence_id)])
    await session.commit()

    citations = await repo.list_citations(claim.claim_id)
    assert [(c.claim_id, c.evidence_id) for c in citations] == [
        (claim.claim_id, evidence.evidence_id)
    ]

    # 复合主键：同一 Claim 不重复绑定同一 Evidence
    await repo.add_citations([Citation(claim_id=claim.claim_id, evidence_id=evidence.evidence_id)])
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    # FK：引用不存在的 Evidence 被拒绝
    await repo.add_citations([Citation(claim_id=claim.claim_id, evidence_id=str(uuid4()))])
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
