"""Evidence 与 Matrix Output 的 PostgreSQL 并发、范围和回滚测试。"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.domain.chunk import Chunk, create_chunk_set
from literature_agent.domain.evidence import create_evidence
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.review import (
    ReviewOutputType,
    ReviewStepKey,
    create_review_output,
    create_review_run,
    create_run_step,
)
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
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
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project("user-1", "Matrix", "")
        await SqlalchemyProjectRepository(session).add(project)
        paper = create_paper("user-1")
        await SqlalchemyPaperRepository(session).add(paper)
        await session.flush()
        version = create_paper_version(
            paper.paper_id,
            "user-1",
            "a" * 64,
            "paper.pdf",
            100,
            "application/pdf",
        )
        await SqlalchemyPaperVersionRepository(session).add(version)
        await session.flush()
        revision = create_parse_revision(version.version_id, "fake", "1", "p" * 64)
        revision = revision.mark_succeeded(project.created_at)
        await SqlalchemyParseRevisionRepository(session).add(revision)
        await session.flush()
        chunk_set = create_chunk_set(revision.revision_id, "c" * 64).mark_ready(project.created_at)
        await SqlalchemyChunkSetRepository(session).add(chunk_set)
        await session.flush()
        chunk = Chunk(
            chunk_id="chunk-1",
            chunk_set_id=chunk_set.chunk_set_id,
            sequence=1,
            text="evidence",
            token_count=10,
            content_hash="d" * 64,
        )
        await SqlalchemyChunkRepository(session).add_many([chunk])
        run = create_run(project.project_id, "user-1", RunType.REVIEW)
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        await SqlalchemyReviewRepository(session).add_review_run(
            create_review_run(
                run_id=run.run_id,
                research_question="问题",
                workflow_version="review.v1",
                model_profile_version="review-default.v1",
                prompt_versions={"evidence_extract": "review-evidence-extraction.v1"},
                config_snapshot={},
            )
        )
        await session.commit()
    return factory, project, paper, version, revision, chunk, run


async def test_concurrent_evidence_and_output_converge_and_remain_scoped(db_engine) -> None:
    factory, project, paper, version, revision, chunk, run = await _seed(db_engine)

    async def write_evidence():
        async with factory() as session:
            proposed = create_evidence(
                run_id=run.run_id,
                project_id=project.project_id,
                paper_id=paper.paper_id,
                version_id=version.version_id,
                parse_revision_id=revision.revision_id,
                chunk_id=chunk.chunk_id,
                section_path=None,
                page_start=None,
                page_end=None,
                excerpt="evidence",
            )
            rows = await SqlalchemyEvidenceRepository(session).get_or_add_many([proposed])
            await session.commit()
            return rows[0]

    first_evidence, second_evidence = await asyncio.gather(write_evidence(), write_evidence())
    assert first_evidence.evidence_id == second_evidence.evidence_id

    async def write_output():
        async with factory() as session:
            proposed = create_review_output(
                review_run_id=run.run_id,
                output_type=ReviewOutputType.EVIDENCE_MATRIX,
                output_key="paper:source-1",
                version=1,
                schema_version="evidence-matrix.v1",
                payload={"rows": []},
                idempotency_key="matrix:source-1:v1",
            )
            row = await SqlalchemyReviewRepository(session).get_or_add_output(proposed)
            await session.commit()
            return row

    first_output, second_output = await asyncio.gather(write_output(), write_output())
    assert first_output.output_id == second_output.output_id
    async with factory() as session:
        repo = SqlalchemyReviewRepository(session)
        assert len(await repo.list_outputs_scoped(run.run_id, project.project_id, "user-1")) == 1
        assert await repo.list_outputs_scoped(run.run_id, project.project_id, "other-user") == []


async def test_output_get_or_add_rolls_back_with_outer_transaction(db_engine) -> None:
    factory, project, _paper, _version, _revision, _chunk, run = await _seed(db_engine)
    proposed = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="matrix",
        version=1,
        schema_version="evidence-matrix.v1",
        payload={"rows": []},
        idempotency_key="rollback:matrix",
    )
    async with factory() as session:
        await SqlalchemyReviewRepository(session).get_or_add_output(proposed)
        await session.rollback()
    async with factory() as session:
        rows = await SqlalchemyReviewRepository(session).list_outputs_scoped(
            run.run_id, project.project_id, "user-1"
        )
    assert rows == []


async def test_conditional_step_advance_does_not_reopen_terminal_state(db_engine) -> None:
    factory, project, _paper, _version, _revision, _chunk, run = await _seed(db_engine)
    pending = create_run_step(
        run_id=run.run_id,
        step_key=ReviewStepKey.BUILD_EVIDENCE_MATRIX,
        sequence=6,
        idempotency_key="matrix-step:v1",
    )
    async with factory() as session:
        repo = SqlalchemyReviewRepository(session)
        await repo.add_step(pending)
        await session.commit()
    running = pending.start()
    async with factory() as session:
        repo = SqlalchemyReviewRepository(session)
        assert await repo.advance_step(running, pending.status.value)
        await session.commit()
    succeeded = running.succeed({"evidence_matrix_output_id": "output-1"})
    async with factory() as session:
        repo = SqlalchemyReviewRepository(session)
        assert await repo.advance_step(succeeded, running.status.value)
        await session.commit()
    async with factory() as session:
        repo = SqlalchemyReviewRepository(session)
        assert not await repo.advance_step(running, pending.status.value)
        await session.commit()
        rows = await repo.list_steps_scoped(run.run_id, project.project_id, "user-1")
    assert rows[0].status.value == "succeeded"
