"""Review v2 候选来源 HITL 应用测试。"""

from dataclasses import replace
from datetime import UTC, datetime

from literature_agent.application.review_source_selection_service import (
    ReviewSourceSelectionService,
)
from literature_agent.domain.queue_outbox import create_outbox_entry
from literature_agent.domain.review import (
    HumanInputRequestKind,
    ReviewSourceStatus,
    ReviewStage,
    create_project_review_source,
    create_review_run,
    create_review_source,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository


async def test_source_candidates_pause_before_download_and_resume_selected_only() -> None:
    runs = FakeRunRepository()
    reviews = FakeReviewRepository()
    events = FakeEventRepository()
    outboxes = FakeOutboxRepository()
    run = replace(
        create_run("project-1", "user-1", RunType.REVIEW).transition_to(RunStatus.RUNNING),
        run_id="review-1",
    )
    review = replace(
        create_review_run(
            run_id=run.run_id,
            research_question="可靠工作流如何恢复？",
            workflow_version="review.v2",
            model_profile_version="review-default.v3",
            prompt_versions={"search_strategy": "search_strategy.v1"},
            config_snapshot={"source_limit": 3, "auto_search_candidates": True},
        ),
        current_stage=ReviewStage.IMPORT_ARXIV_PAPERS,
    )
    await runs.add(run)
    reviews.authorize_run(run.run_id, run.project_id, run.owner_id)
    await reviews.add_review_run(review)
    await reviews.add_source(
        create_project_review_source(
            review_run_id=run.run_id,
            paper_id="paper-1",
            paper_version_id="version-1",
            rank=1,
            metadata_snapshot={"title": "项目论文"},
        )
    )
    first = create_review_source(
        review_run_id=run.run_id,
        arxiv_id="2601.00001",
        arxiv_version="v1",
        rank=2,
        metadata_snapshot={"title": "候选一", "abstract": "摘要一", "page_count": None},
    )
    second = create_review_source(
        review_run_id=run.run_id,
        arxiv_id="2601.00002",
        arxiv_version="v1",
        rank=3,
        metadata_snapshot={"title": "候选二", "abstract": "摘要二", "page_count": 12},
    )
    await reviews.add_source(first)
    await reviews.add_source(second)
    outbox = await outboxes.add(create_outbox_entry(run.run_id))
    assert await outboxes.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
    service = ReviewSourceSelectionService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        event_repo_factory=lambda _session: events,
        outbox_repo_factory=lambda _session: outboxes,
    )

    output, request = await service.pause(
        run_id=run.run_id,
        project_id=run.project_id,
        owner_id=run.owner_id,
        correlation_id="corr-pause",
    )

    waiting = await runs.get_by_id(run.run_id)
    assert waiting is not None and waiting.status is RunStatus.WAITING_INPUT
    assert request.request_kind is HumanInputRequestKind.SOURCE_SELECTION
    assert output.payload["max_selected"] == 2
    assert output.payload["candidates"][1]["page_count"] == 12
    assert reviews.review_runs[run.run_id].current_stage is ReviewStage.REVIEW_SOURCES

    result = await service.submit(
        run_id=run.run_id,
        project_id=run.project_id,
        owner_id=run.owner_id,
        request_id=request.request_id,
        request_version=request.request_version,
        candidate_output_id=output.output_id,
        selected_source_ids=[second.source_id],
        idempotency_key="select-1",
        correlation_id="corr-submit",
    )

    resumed = await runs.get_by_id(run.run_id)
    sources = await reviews.list_sources_scoped(run.run_id, run.project_id, run.owner_id)
    by_id = {item.source_id: item for item in sources}
    assert resumed is not None and resumed.status is RunStatus.QUEUED
    assert reviews.review_runs[run.run_id].current_stage is ReviewStage.IMPORT_ARXIV_PAPERS
    assert by_id[first.source_id].status is ReviewSourceStatus.REJECTED
    assert by_id[second.source_id].status is ReviewSourceStatus.DISCOVERED
    assert result.selected_source_ids == (second.source_id,)
