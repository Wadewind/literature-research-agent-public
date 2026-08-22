"""Review 依赖对账的 PostgreSQL 事务与并发集成测试。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from literature_agent.application.review_dependency_service import ReviewDependencyReconciler
from literature_agent.application.waiting_run_resume_service import WaitingRunResumeService
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.review import (
    ReviewDependencyStatus,
    ReviewDependencyType,
    ReviewSourceStatus,
    create_review_dependency,
    create_review_run,
    create_review_source,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
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
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


async def _seed_ready_dependency(
    factory: async_sessionmaker[AsyncSession], project_id: str
) -> tuple[str, str]:
    """持久化等待中的父 Run 与已经形成 ready ChunkSet 的来源。"""
    async with factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        review_repo = SqlalchemyReviewRepository(session)
        parent = replace(
            create_run(project_id, "user-1", RunType.REVIEW),
            status=RunStatus.WAITING_DEPENDENCY,
            event_sequence=2,
        )
        ingestion = replace(
            create_run(project_id, "user-1", RunType.INGESTION),
            status=RunStatus.SUCCEEDED,
        )
        paper = create_paper("user-1")
        version = create_paper_version(
            paper_id=paper.paper_id,
            owner_id="user-1",
            file_hash="a" * 64,
            storage_key="cache/a.pdf",
            size_bytes=100,
            content_type="application/pdf",
            display_filename="a.pdf",
            ingestion_run_id=ingestion.run_id,
        )
        profile = ParseProfile("fake", "1", {})
        revision = create_parse_revision(
            version.version_id,
            profile.parser_name,
            profile.parser_version,
            profile.profile_hash,
            profile.config,
        ).mark_succeeded(datetime.now(UTC))
        chunk_set = create_chunk_set(revision.revision_id, "chunk-profile").mark_ready(
            datetime.now(UTC)
        )
        source = create_review_source(
            review_run_id=parent.run_id,
            arxiv_id="2401.00001",
            arxiv_version="v1",
            rank=1,
            metadata_snapshot={},
        ).mark_importing(paper.paper_id, version.version_id)

        await run_repo.add(parent)
        await run_repo.add(ingestion)
        await SqlalchemyPaperRepository(session).add(paper)
        await session.flush()
        await SqlalchemyPaperVersionRepository(session).add(version)
        await session.flush()
        await SqlalchemyProjectPaperRepository(session).add(
            create_project_paper(project_id, paper.paper_id, version.version_id)
        )
        await SqlalchemyParseRevisionRepository(session).add(revision)
        await session.flush()
        await SqlalchemyPaperVersionRepository(session).set_current_parse_revision(
            version.version_id, revision.revision_id
        )
        await SqlalchemyChunkSetRepository(session).add(chunk_set)
        await review_repo.add_review_run(
            create_review_run(
                run_id=parent.run_id,
                research_question="依赖恢复",
                workflow_version="review.v1",
                model_profile_version="review-default.v1",
                prompt_versions={"outline": "outline.v1"},
                config_snapshot={"minimum_ready_papers": 1},
            )
        )
        await review_repo.add_source(source)
        await review_repo.add_dependency(
            create_review_dependency(
                parent_run_id=parent.run_id,
                dependency_type=ReviewDependencyType.PAPER_VERSION,
                target_paper_version_id=version.version_id,
            )
        )
        await review_repo.add_dependency(
            create_review_dependency(
                parent_run_id=parent.run_id,
                dependency_type=ReviewDependencyType.RUN,
                target_run_id=ingestion.run_id,
            )
        )
        outbox = create_outbox_entry(parent.run_id)
        outbox_repo = SqlalchemyOutboxRepository(session)
        await outbox_repo.add(outbox)
        await session.flush()
        assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
        await session.commit()
        return parent.run_id, source.source_id


def _service(
    factory: async_sessionmaker[AsyncSession],
    *,
    outbox_repository=SqlalchemyOutboxRepository,
) -> ReviewDependencyReconciler[AsyncSession]:
    resume = WaitingRunResumeService(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=outbox_repository,
    )
    return ReviewDependencyReconciler(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        waiting_resume_service=resume,
        batch_size=20,
    )


async def test_ready_source_and_resume_commit_atomically(db_engine, project: str) -> None:
    """来源、依赖、Event、父状态和 Outbox 在一次提交后同时可见。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run_id, _source_id = await _seed_ready_dependency(factory, project)

    assert await _service(factory).reconcile_waiting() == 1

    async with factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(run_id)
        sources = await SqlalchemyReviewRepository(session).list_sources_scoped(
            run_id, project, "user-1"
        )
        dependencies = await SqlalchemyReviewRepository(session).list_dependencies_scoped(
            run_id, project, "user-1"
        )
        events = await SqlalchemyEventRepository(session).list_by_run(run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(run_id)
    assert run is not None and run.status is RunStatus.QUEUED
    assert sources[0].status is ReviewSourceStatus.READY
    assert all(dep.status is ReviewDependencyStatus.SATISFIED for dep in dependencies)
    assert [event.event_type for event in events] == [
        "review_source_ready",
        "dependency_wait_completed",
    ]
    assert outbox is not None and outbox.status is OutboxStatus.PENDING


async def test_concurrent_reconcilers_resume_only_once(db_engine, project: str) -> None:
    """两个扫描器命中同一父 Run 时由父行锁与状态条件串行化。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run_id, _source_id = await _seed_ready_dependency(factory, project)

    results = await asyncio.gather(
        _service(factory).reconcile_waiting(),
        _service(factory).reconcile_waiting(),
    )

    assert sum(results) == 1
    async with factory() as session:
        events = await SqlalchemyEventRepository(session).list_by_run(run_id)
    assert [event.event_type for event in events].count("review_source_ready") == 1
    assert [event.event_type for event in events].count("dependency_wait_completed") == 1


async def test_schedule_again_exception_rolls_back_dependency_effects(
    db_engine, project: str
) -> None:
    """恢复投递失败时来源、依赖、Event 与父状态均不得部分提交。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run_id, source_id = await _seed_ready_dependency(factory, project)

    class RaisingOutboxRepository(SqlalchemyOutboxRepository):
        async def schedule_again(self, run_id: str) -> bool:
            raise RuntimeError(f"schedule_again_failed:{run_id}")

    with pytest.raises(RuntimeError, match="schedule_again_failed"):
        await _service(
            factory, outbox_repository=RaisingOutboxRepository
        ).reconcile_run(run_id)

    async with factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(run_id)
        source = await SqlalchemyReviewRepository(session).get_source_scoped_for_update(
            source_id, run_id, project, "user-1"
        )
        dependencies = await SqlalchemyReviewRepository(session).list_dependencies_scoped(
            run_id, project, "user-1"
        )
        events = await SqlalchemyEventRepository(session).list_by_run(run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(run_id)
    assert run is not None and run.status is RunStatus.WAITING_DEPENDENCY
    assert source is not None and source.status is ReviewSourceStatus.IMPORTING
    assert all(dep.status is ReviewDependencyStatus.PENDING for dep in dependencies)
    assert events == []
    assert outbox is not None and outbox.status is OutboxStatus.DISPATCHED
