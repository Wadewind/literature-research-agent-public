"""Review 论文依赖等待与恢复应用服务测试。"""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

from literature_agent.application.review_dependency_service import (
    ReviewDependencyReconciler,
    ReviewDependencyWaitService,
)
from literature_agent.application.waiting_run_resume_service import WaitingRunResumeService
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
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
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository


class Harness:
    """集中持有应用测试使用的共享 Fake。"""

    def __init__(self) -> None:
        self.runs = FakeRunRepository()
        self.events = FakeEventRepository()
        self.outbox = FakeOutboxRepository()
        self.reviews = FakeReviewRepository()
        self.papers = FakePaperRepository()
        self.versions = FakePaperVersionRepository()
        self.project_papers = FakeProjectPaperRepository()
        self.revisions = FakeParseRevisionRepository()
        self.chunk_sets = FakeChunkSetRepository(self.revisions)
        self.notifier = RecordingNotifier()
        self.resume = WaitingRunResumeService(
            session_factory=fake_session,
            run_repo_factory=lambda _s: self.runs,
            event_repo_factory=lambda _s: self.events,
            outbox_repo_factory=lambda _s: self.outbox,
        )

    def reconciler(self) -> ReviewDependencyReconciler:
        return ReviewDependencyReconciler(
            session_factory=fake_session,
            run_repo_factory=lambda _s: self.runs,
            event_repo_factory=lambda _s: self.events,
            review_repo_factory=lambda _s: self.reviews,
            paper_repo_factory=lambda _s: self.papers,
            paper_version_repo_factory=lambda _s: self.versions,
            parse_revision_repo_factory=lambda _s: self.revisions,
            project_paper_repo_factory=lambda _s: self.project_papers,
            chunk_set_repo_factory=lambda _s: self.chunk_sets,
            waiting_resume_service=self.resume,
            batch_size=20,
            event_notifier=self.notifier,
        )

    async def seed_review(self, *, source_count: int = 1) -> str:
        run = replace(
            create_run("project-1", "owner-1", RunType.REVIEW),
            status=RunStatus.RUNNING,
            event_sequence=2,
        )
        await self.runs.add(run)
        self.reviews.authorize_run(run.run_id, run.project_id, run.owner_id)
        await self.reviews.add_review_run(
            create_review_run(
                run_id=run.run_id,
                research_question="测试问题",
                workflow_version="review.v1",
                model_profile_version="review-default.v1",
                prompt_versions={"search": "search.v1"},
                config_snapshot={"minimum_ready_papers": 1},
            )
        )
        entry = create_outbox_entry(run.run_id)
        await self.outbox.add(entry)
        assert await self.outbox.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))
        for rank in range(1, source_count + 1):
            await self.reviews.add_source(
                create_review_source(
                    review_run_id=run.run_id,
                    arxiv_id=f"2401.{rank:05d}",
                    arxiv_version="v1",
                    rank=rank,
                    metadata_snapshot={},
                )
            )
        return run.run_id

    async def bind_importing_source(
        self,
        review_run_id: str,
        rank: int = 1,
        *,
        ingestion_project_id: str = "project-1",
        add_run_dependency: bool = True,
    ):
        paper = create_paper("owner-1")
        ingestion = replace(
            create_run(ingestion_project_id, "owner-1", RunType.INGESTION),
            status=RunStatus.RUNNING,
        )
        version = create_paper_version(
            paper_id=paper.paper_id,
            owner_id="owner-1",
            file_hash=str(rank) * 64,
            storage_key=f"paper-{rank}.pdf",
            size_bytes=100,
            content_type="application/pdf",
            display_filename=f"paper-{rank}.pdf",
            ingestion_run_id=ingestion.run_id,
        )
        await self.papers.add(paper)
        await self.runs.add(ingestion)
        await self.versions.add(version)
        await self.project_papers.add(
            create_project_paper("project-1", paper.paper_id, version.version_id)
        )
        source = (await self.reviews.list_sources_scoped(
            review_run_id, "project-1", "owner-1"
        ))[rank - 1]
        source = source.mark_importing(paper.paper_id, version.version_id)
        await self.reviews.save_source(source)
        await self.reviews.add_dependency(
            create_review_dependency(
                parent_run_id=review_run_id,
                dependency_type=ReviewDependencyType.PAPER_VERSION,
                target_paper_version_id=version.version_id,
            )
        )
        if add_run_dependency:
            await self.reviews.add_dependency(
                create_review_dependency(
                    parent_run_id=review_run_id,
                    dependency_type=ReviewDependencyType.RUN,
                    target_run_id=ingestion.run_id,
                )
            )
        return source, paper, version, ingestion


class RecordingNotifier:
    """记录提交后轻量通知的测试替身。"""

    def __init__(self) -> None:
        self.run_ids: list[str] = []

    async def notify(self, run_id: str) -> None:
        self.run_ids.append(run_id)

    def subscribe(self, run_id: str) -> AsyncIterator[None]:
        async def never() -> AsyncIterator[None]:
            return
            yield

        return never()

    async def aclose(self) -> None:
        return None


async def test_pause_enters_waiting_without_resetting_outbox() -> None:
    """Worker 暂停只提交等待状态和 Event，Outbox 继续保持 dispatched。"""
    h = Harness()
    run_id = await h.seed_review()
    service = ReviewDependencyWaitService(
        session_factory=fake_session,
        run_repo_factory=lambda _s: h.runs,
        event_repo_factory=lambda _s: h.events,
        review_repo_factory=lambda _s: h.reviews,
    )

    waiting = await service.pause(run_id, "project-1", "owner-1", "wait-1")

    assert waiting.status is RunStatus.WAITING_DEPENDENCY
    assert [event.event_type for event in h.events._events] == ["dependency_wait_started"]
    outbox = await h.outbox.get_by_run_id(run_id)
    assert outbox is not None and outbox.status is OutboxStatus.DISPATCHED


async def test_ready_chunk_set_updates_source_dependencies_and_resumes() -> None:
    """PaperVersion 出现 ready ChunkSet 后，所有事实与 schedule_again 同事务推进。"""
    h = Harness()
    run_id = await h.seed_review()
    source, _paper, version, ingestion = await h.bind_importing_source(run_id)
    await h.runs.update_status(ingestion.run_id, RunStatus.RUNNING, RunStatus.SUCCEEDED, 2)
    from literature_agent.domain.parse_profile import ParseProfile
    from literature_agent.domain.parse_revision import create_parse_revision

    profile = ParseProfile("fake", "1", {})
    revision = create_parse_revision(
        version.version_id,
        profile.parser_name,
        profile.parser_version,
        profile.profile_hash,
        profile.config,
    ).mark_succeeded(datetime.now(UTC))
    await h.revisions.add(revision)
    await h.versions.set_current_parse_revision(version.version_id, revision.revision_id)
    chunk_set = create_chunk_set(revision.revision_id, "profile-hash")
    await h.chunk_sets.add(chunk_set.mark_ready(datetime.now(UTC)))
    await h.runs.update_status(run_id, RunStatus.RUNNING, RunStatus.WAITING_DEPENDENCY, 3)

    assert await h.reconciler().reconcile_waiting() == 1

    parent = await h.runs.get_by_id(run_id)
    assert parent is not None and parent.status is RunStatus.QUEUED
    refreshed = await h.reviews.get_source_scoped_for_update(
        source.source_id, run_id, "project-1", "owner-1"
    )
    assert refreshed is not None and refreshed.status is ReviewSourceStatus.READY
    dependencies = await h.reviews.list_dependencies_scoped(
        run_id, "project-1", "owner-1"
    )
    assert all(dep.status is ReviewDependencyStatus.SATISFIED for dep in dependencies)
    assert any(dep.target_chunk_set_id == chunk_set.chunk_set_id for dep in dependencies)
    assert [event.event_type for event in h.events._events] == [
        "review_source_ready",
        "dependency_wait_completed",
    ]
    outbox = await h.outbox.get_by_run_id(run_id)
    assert outbox is not None and outbox.status is OutboxStatus.PENDING
    assert outbox.attempt_count == 0


async def test_partial_failure_waits_for_all_sources_then_resumes() -> None:
    """部分来源失败不提前固定 Evidence 集，全部终态后使用成功来源继续。"""
    h = Harness()
    run_id = await h.seed_review(source_count=2)
    first, _paper, version, ingestion = await h.bind_importing_source(run_id, 1)
    second, *_ = await h.bind_importing_source(run_id, 2)
    await h.runs.update_status(ingestion.run_id, RunStatus.RUNNING, RunStatus.FAILED, 2)
    await h.runs.update_status(run_id, RunStatus.RUNNING, RunStatus.WAITING_DEPENDENCY, 3)

    assert await h.reconciler().reconcile_waiting() == 0
    parent = await h.runs.get_by_id(run_id)
    assert parent is not None and parent.status is RunStatus.WAITING_DEPENDENCY
    failed = await h.reviews.get_source_scoped_for_update(
        first.source_id, run_id, "project-1", "owner-1"
    )
    assert failed is not None and failed.status is ReviewSourceStatus.FAILED
    assert h.notifier.run_ids == [run_id]

    # 第二篇随后形成 ready ChunkSet，下一轮才恢复。
    second_version = await h.versions.get_by_id(second.paper_version_id or "")
    assert second_version is not None
    from literature_agent.domain.parse_profile import ParseProfile
    from literature_agent.domain.parse_revision import create_parse_revision

    profile = ParseProfile("fake", "1", {})
    revision = create_parse_revision(
        second_version.version_id,
        profile.parser_name,
        profile.parser_version,
        profile.profile_hash,
        profile.config,
    ).mark_succeeded(datetime.now(UTC))
    await h.revisions.add(revision)
    await h.chunk_sets.add(
        create_chunk_set(revision.revision_id, "profile").mark_ready(datetime.now(UTC))
    )
    assert await h.reconciler().reconcile_waiting() == 1
    parent = await h.runs.get_by_id(run_id)
    assert parent is not None and parent.status is RunStatus.QUEUED


async def test_all_failed_and_zero_source_reviews_end_with_stable_error() -> None:
    """没有可用论文的等待 Run 不得永久悬挂。"""
    for source_count in (0, 1):
        h = Harness()
        run_id = await h.seed_review(source_count=source_count)
        if source_count:
            _source, _paper, _version, ingestion = await h.bind_importing_source(run_id)
            await h.runs.update_status(
                ingestion.run_id, RunStatus.RUNNING, RunStatus.FAILED, 2
            )
        await h.runs.update_status(
            run_id, RunStatus.RUNNING, RunStatus.WAITING_DEPENDENCY, 3
        )

        assert await h.reconciler().reconcile_waiting() == 1
        parent = await h.runs.get_by_id(run_id)
        assert parent is not None and parent.status is RunStatus.FAILED
        assert h.events._events[-1].event_type == "run_failed"
        assert h.events._events[-1].payload["error"]["type"] == "no_reviewable_papers"
        outbox = await h.outbox.get_by_run_id(run_id)
        assert outbox is not None and outbox.status is OutboxStatus.DISPATCHED


async def test_repeated_reconcile_does_not_duplicate_events_or_resume() -> None:
    """父 Run 状态条件使重复扫描只有一次业务效果。"""
    h = Harness()
    run_id = await h.seed_review(source_count=0)
    await h.runs.update_status(run_id, RunStatus.RUNNING, RunStatus.WAITING_DEPENDENCY, 3)

    service = h.reconciler()
    assert await service.reconcile_waiting() == 1
    assert await service.reconcile_waiting() == 0
    assert len(h.events._events) == 1


async def test_indexing_succeeded_without_ready_chunk_set_is_terminal_invariant_error() -> None:
    """Indexing 已成功却没有 ready ChunkSet 时不能继续等待。"""
    h = Harness()
    run_id = await h.seed_review()
    source, _paper, version, ingestion = await h.bind_importing_source(run_id)
    await h.runs.update_status(ingestion.run_id, RunStatus.RUNNING, RunStatus.SUCCEEDED, 2)
    from literature_agent.domain.parse_profile import ParseProfile
    from literature_agent.domain.parse_revision import create_parse_revision

    profile = ParseProfile("fake", "1", {})
    revision = create_parse_revision(
        version.version_id,
        profile.parser_name,
        profile.parser_version,
        profile.profile_hash,
        profile.config,
    ).mark_succeeded(datetime.now(UTC))
    await h.revisions.add(revision)
    await h.versions.set_current_parse_revision(version.version_id, revision.revision_id)
    indexing = replace(
        create_run(
            "project-1",
            "owner-1",
            RunType.INDEXING,
            {"parse_revision_id": revision.revision_id},
        ),
        status=RunStatus.SUCCEEDED,
    )
    await h.runs.add(indexing)
    await h.runs.update_status(run_id, RunStatus.RUNNING, RunStatus.WAITING_DEPENDENCY, 3)

    assert await h.reconciler().reconcile_waiting() == 1
    failed = await h.reviews.get_source_scoped_for_update(
        source.source_id, run_id, "project-1", "owner-1"
    )
    assert failed is not None
    assert failed.failure_code == "chunk_set_missing_after_indexing"


async def test_cross_project_reused_version_waits_without_run_dependency() -> None:
    """跨 Project 复用 Version 只观察 Version/ChunkSet，不创建或要求 RUN 依赖。"""
    h = Harness()
    run_id = await h.seed_review()
    _source, _paper, version, _ingestion = await h.bind_importing_source(
        run_id,
        ingestion_project_id="other-project",
        add_run_dependency=False,
    )
    from literature_agent.domain.parse_profile import ParseProfile
    from literature_agent.domain.parse_revision import create_parse_revision

    profile = ParseProfile("fake", "1", {})
    revision = create_parse_revision(
        version.version_id,
        profile.parser_name,
        profile.parser_version,
        profile.profile_hash,
        profile.config,
    ).mark_succeeded(datetime.now(UTC))
    await h.revisions.add(revision)
    await h.chunk_sets.add(
        create_chunk_set(revision.revision_id, "profile").mark_ready(datetime.now(UTC))
    )
    await h.runs.update_status(run_id, RunStatus.RUNNING, RunStatus.WAITING_DEPENDENCY, 3)

    assert await h.reconciler().reconcile_waiting() == 1
    dependencies = await h.reviews.list_dependencies_scoped(
        run_id, "project-1", "owner-1"
    )
    assert all(dep.dependency_type is not ReviewDependencyType.RUN for dep in dependencies)


async def test_existing_run_dependency_rejects_child_scope_mismatch() -> None:
    """已有 RUN 依赖必须仍指向同 owner/Project 的 Ingestion Run。"""
    h = Harness()
    run_id = await h.seed_review()
    source, _paper, _version, ingestion = await h.bind_importing_source(run_id)
    await h.runs.add(replace(ingestion, owner_id="other-owner"))
    await h.runs.update_status(run_id, RunStatus.RUNNING, RunStatus.WAITING_DEPENDENCY, 3)

    assert await h.reconciler().reconcile_waiting() == 1

    failed_source = await h.reviews.get_source_scoped_for_update(
        source.source_id, run_id, "project-1", "owner-1"
    )
    assert failed_source is not None
    assert failed_source.failure_code == "ingestion_run_scope_mismatch"
    dependencies = await h.reviews.list_dependencies_scoped(
        run_id, "project-1", "owner-1"
    )
    run_dependency = next(
        dep for dep in dependencies if dep.dependency_type is ReviewDependencyType.RUN
    )
    assert run_dependency.status is ReviewDependencyStatus.FAILED
    assert run_dependency.failure_code == "dependency_run_scope_mismatch"
