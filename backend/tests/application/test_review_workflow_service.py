"""ReviewWorkflowService 应用测试。"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from literature_agent.application.review_workflow_service import (
    DEFAULT_CONFIG_SNAPSHOT,
    ReviewWorkflowService,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.exceptions import (
    IdempotencyConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from literature_agent.domain.paper import PaperTitleSource, create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.review import (
    ReviewSourceKind,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
)
from literature_agent.domain.run import RunStatus, RunType
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_idempotency_repository import FakeIdempotencyRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository


def _service():
    project_repo = FakeProjectRepository()
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    idempotency_repo = FakeIdempotencyRepository()
    review_repo = FakeReviewRepository()
    paper_repo = FakePaperRepository()
    paper_version_repo = FakePaperVersionRepository()
    project_paper_repo = FakeProjectPaperRepository()
    chunk_set_repo = FakeChunkSetRepository()
    service = ReviewWorkflowService(
        session_factory=fake_session,
        project_repo_factory=lambda _session: project_repo,
        run_repo_factory=lambda _session: run_repo,
        event_repo_factory=lambda _session: event_repo,
        outbox_repo_factory=lambda _session: outbox_repo,
        idempotency_repo_factory=lambda _session: idempotency_repo,
        review_repo_factory=lambda _session: review_repo,
        paper_repo_factory=lambda _session: paper_repo,
        paper_version_repo_factory=lambda _session: paper_version_repo,
        project_paper_repo_factory=lambda _session: project_paper_repo,
        chunk_set_repo_factory=lambda _session: chunk_set_repo,
    )
    return (
        service,
        project_repo,
        run_repo,
        event_repo,
        outbox_repo,
        review_repo,
    )


async def test_create_review_run_is_atomic_business_bundle() -> None:
    """创建闭环应同时形成通用 Run、ReviewRun、Event 和 Outbox。"""
    service, projects, runs, events, outboxes, reviews = _service()
    project = create_project("user-1", "HITL", "")
    await projects.add(project)

    result = await service.create_review_run(
        actor=ActorContext(owner_id="user-1"),
        project_id=project.project_id,
        research_question="LangGraph 如何可靠恢复？",
        idempotency_key="review-create-1",
        correlation_id="corr-1",
    )

    run = await runs.get_by_id(result.run_id)
    review = reviews.review_runs.get(result.run_id)
    event_rows = await events.list_by_run(result.run_id)
    outbox = await outboxes.get_by_run_id(result.run_id)
    assert run is not None and run.run_type == RunType.REVIEW.value
    assert run.status is RunStatus.QUEUED and run.event_sequence == 2
    assert review is not None and review.research_question == "LangGraph 如何可靠恢复？"
    assert review.current_stage is ReviewStage.FORMULATE_SEARCH_STRATEGY
    assert review.model_profile_version == "review-default.v3"
    assert review.prompt_versions["evidence_extract"] == "review-evidence-extraction.v1"
    assert review.config_snapshot["source_limit"] == 3
    assert review.config_snapshot["section_output_token_limit"] == 8_000
    assert review.config_snapshot["consistency_output_token_limit"] == 8_000
    assert event_rows[0].event_type == "review_run_created"
    assert event_rows[0].payload == {
        "status": "queued",
        "workflow_version": "review.v2",
        "current_stage": "formulate_search_strategy",
    }
    assert len(reviews.steps) == 1
    assert reviews.steps[0].step_key is ReviewStepKey.VALIDATE_REQUEST
    assert reviews.steps[0].status is ReviewStepStatus.SUCCEEDED
    assert outbox is not None and outbox.run_id == result.run_id


async def test_create_review_run_idempotency_replay_and_conflict() -> None:
    """同键同请求返回原 Run；同键不同问题产生冲突。"""
    service, projects, *_ = _service()
    project = create_project("user-1", "HITL", "")
    await projects.add(project)
    kwargs = {
        "actor": ActorContext(owner_id="user-1"),
        "project_id": project.project_id,
        "research_question": "问题一",
        "idempotency_key": "review-create-1",
        "correlation_id": "corr-1",
    }

    first = await service.create_review_run(**kwargs)
    replay = await service.create_review_run(**kwargs)
    assert replay.run_id == first.run_id
    assert replay.reused is True

    with pytest.raises(IdempotencyConflictError):
        await service.create_review_run(**{**kwargs, "research_question": "问题二"})


async def test_output_token_profile_participates_in_request_fingerprint(monkeypatch) -> None:
    service, projects, *_ = _service()
    project = create_project("user-1", "HITL", "")
    await projects.add(project)
    kwargs = {
        "actor": ActorContext(owner_id="user-1"),
        "project_id": project.project_id,
        "research_question": "问题一",
        "idempotency_key": "review-profile-fingerprint",
        "correlation_id": "corr-1",
    }
    await service.create_review_run(**kwargs)
    monkeypatch.setitem(DEFAULT_CONFIG_SNAPSHOT, "section_output_token_limit", 8_001)

    with pytest.raises(IdempotencyConflictError):
        await service.create_review_run(**kwargs)


async def test_create_review_run_enforces_owner_and_active_project() -> None:
    """跨 owner 或归档 Project 均不得创建 Review Run。"""
    service, projects, *_ = _service()
    project = create_project("user-1", "HITL", "")
    await projects.add(project)

    with pytest.raises(ProjectNotFoundError):
        await service.create_review_run(
            ActorContext(owner_id="user-2"),
            project.project_id,
            "问题",
            "other-owner",
            "corr-1",
        )

    await projects.update(project.archive())
    with pytest.raises(ProjectArchivedError):
        await service.create_review_run(
            ActorContext(owner_id="user-1"),
            project.project_id,
            "问题",
            "archived",
            "corr-2",
        )


async def test_review_v2_requires_a_selected_or_automatic_source() -> None:
    service, projects, *_ = _service()
    project = create_project("user-1", "HITL", "")
    await projects.add(project)

    with pytest.raises(ValueError, match="至少选择一篇"):
        await service.create_review_run(
            ActorContext("user-1"),
            project.project_id,
            "研究问题",
            "without-source",
            "corr-1",
            [],
            False,
        )


async def test_review_v2_fixes_ready_project_versions_as_sources() -> None:
    projects = FakeProjectRepository()
    runs = FakeRunRepository()
    events = FakeEventRepository()
    outboxes = FakeOutboxRepository()
    idempotency = FakeIdempotencyRepository()
    reviews = FakeReviewRepository()
    papers = FakePaperRepository()
    versions = FakePaperVersionRepository()
    relations = FakeProjectPaperRepository()
    revisions = FakeParseRevisionRepository()
    chunk_sets = FakeChunkSetRepository(revisions)
    service = ReviewWorkflowService(
        session_factory=fake_session,
        project_repo_factory=lambda _session: projects,
        run_repo_factory=lambda _session: runs,
        event_repo_factory=lambda _session: events,
        outbox_repo_factory=lambda _session: outboxes,
        idempotency_repo_factory=lambda _session: idempotency,
        review_repo_factory=lambda _session: reviews,
        paper_repo_factory=lambda _session: papers,
        paper_version_repo_factory=lambda _session: versions,
        project_paper_repo_factory=lambda _session: relations,
        chunk_set_repo_factory=lambda _session: chunk_sets,
    )
    project = create_project("user-1", "HITL", "")
    paper = create_paper(
        "user-1", title="可靠工作流", title_source=PaperTitleSource.PARSED_DOCUMENT
    )
    version = replace(
        create_paper_version(
            paper.paper_id,
            "user-1",
            "a" * 64,
            "papers/a.pdf",
            100,
            "application/pdf",
        ),
        version_id="version-1",
    )
    revision = create_parse_revision("version-1", "fake", "1", "profile").mark_succeeded(
        datetime.now(UTC)
    )
    chunk_set = create_chunk_set(revision.revision_id, "chunk-profile").mark_ready(
        datetime.now(UTC)
    )
    await projects.add(project)
    await papers.add(paper)
    await versions.add(version)
    await relations.add(
        create_project_paper(project.project_id, paper.paper_id, version.version_id)
    )
    await revisions.add(revision)
    await chunk_sets.add(chunk_set)

    result = await service.create_review_run(
        ActorContext("user-1"),
        project.project_id,
        "研究问题",
        "selected-source",
        "corr-1",
        [version.version_id],
        False,
    )

    sources = reviews.sources
    review = reviews.review_runs[result.run_id]
    assert len(sources) == 1
    assert sources[0].source_kind is ReviewSourceKind.PROJECT
    assert sources[0].paper_version_id == version.version_id
    assert review.workflow_version == "review.v2"
    assert review.config_snapshot["auto_search_candidates"] is False
