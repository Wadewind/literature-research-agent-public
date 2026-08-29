"""ReviewWorkflowService 应用测试。"""

import pytest

from literature_agent.application.review_workflow_service import (
    DEFAULT_CONFIG_SNAPSHOT,
    ReviewWorkflowService,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    IdempotencyConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from literature_agent.domain.project import create_project
from literature_agent.domain.review import ReviewStage, ReviewStepKey, ReviewStepStatus
from literature_agent.domain.run import RunStatus, RunType
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_idempotency_repository import FakeIdempotencyRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
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
    service = ReviewWorkflowService(
        session_factory=fake_session,
        project_repo_factory=lambda _session: project_repo,
        run_repo_factory=lambda _session: run_repo,
        event_repo_factory=lambda _session: event_repo,
        outbox_repo_factory=lambda _session: outbox_repo,
        idempotency_repo_factory=lambda _session: idempotency_repo,
        review_repo_factory=lambda _session: review_repo,
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
        "workflow_version": "review.v1",
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
