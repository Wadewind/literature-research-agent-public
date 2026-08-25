"""ReviewQueryService 列表读路径测试。"""

import pytest

from literature_agent.application.review_query_service import ReviewQueryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import RunNotFoundError
from literature_agent.domain.review import ReviewOutputType, create_review_output, create_review_run
from literature_agent.domain.run import RunType, create_run
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository
from tests.fakes.fake_storage import FakeStorage


async def test_list_reviews_returns_only_scoped_review_runs_in_stable_order() -> None:
    reviews = FakeReviewRepository()
    first = create_run("project-1", "user-1", RunType.REVIEW)
    second = create_run("project-1", "user-1", RunType.REVIEW)
    hidden = create_run("project-2", "user-1", RunType.REVIEW)
    wrong_type = create_run("project-1", "user-1", RunType.INGESTION)
    for run in (first, second, hidden, wrong_type):
        reviews.runs[run.run_id] = run
        reviews.review_runs[run.run_id] = create_review_run(
            run_id=run.run_id,
            research_question=f"question-{run.run_id}",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"outline": "outline_generate.v1"},
            config_snapshot={"source_limit": 10},
        )
    service = ReviewQueryService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: FakeRunRepository(),
        review_repo_factory=lambda _session: reviews,
        storage=FakeStorage(),
    )

    rows = await service.list_reviews(ActorContext("user-1"), "project-1")

    assert [run.run_id for run, _ in rows] == [second.run_id, first.run_id]
    assert await service.list_reviews(ActorContext("user-2"), "project-1") == []


async def test_output_selects_canonical_key_before_version() -> None:
    """单篇中间 Output 的高版本不能覆盖 Review 的聚合 Matrix。"""
    reviews = FakeReviewRepository()
    run = create_run("project-1", "user-1", RunType.REVIEW)
    reviews.review_runs[run.run_id] = create_review_run(
        run_id=run.run_id,
        research_question="研究问题",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={"evidence_extract": "review-evidence-extraction.v1"},
        config_snapshot={"source_limit": 10},
    )
    reviews.authorize_run(run.run_id, "project-1", "user-1")
    runs = FakeRunRepository()
    await runs.add(run)
    aggregate = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="evidence-matrix",
        version=1,
        schema_version="evidence-matrix.v1",
        payload={"rows": [], "summary": {"valid_papers": 1}},
        idempotency_key="matrix:aggregate:1",
    )
    per_paper = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="paper:source-1",
        version=2,
        schema_version="evidence-matrix.v1",
        payload={"rows": [], "summary": {"valid_papers": 999}},
        idempotency_key="matrix:paper:2",
    )
    reviews.outputs.extend([aggregate, per_paper])
    service = ReviewQueryService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        storage=FakeStorage(),
    )

    selected = await service.output(
        ActorContext("user-1"),
        "project-1",
        run.run_id,
        ReviewOutputType.EVIDENCE_MATRIX,
        "evidence-matrix",
    )

    assert selected == aggregate


async def test_sections_returns_latest_version_per_key_in_stable_order() -> None:
    reviews = FakeReviewRepository()
    run = create_run("project-1", "user-1", RunType.REVIEW)
    reviews.runs[run.run_id] = run
    reviews.review_runs[run.run_id] = create_review_run(
        run_id=run.run_id,
        research_question="研究问题",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={"section": "section_draft.v1"},
        config_snapshot={"source_limit": 10},
    )
    reviews.authorize_run(run.run_id, "project-1", "user-1")
    runs = FakeRunRepository()
    await runs.add(run)
    for output_type, output_key, version in (
        (ReviewOutputType.SECTION, "section:results", 1),
        (ReviewOutputType.OUTLINE, "outline", 1),
        (ReviewOutputType.SECTION, "section:methods", 1),
        (ReviewOutputType.SECTION, "section:methods", 2),
    ):
        reviews.outputs.append(
            create_review_output(
                review_run_id=run.run_id,
                output_type=output_type,
                output_key=output_key,
                version=version,
                schema_version=(
                    "section.v1"
                    if output_type is ReviewOutputType.SECTION
                    else "outline.v1"
                ),
                payload={"section_key": output_key.split(":")[-1], "version": version},
                idempotency_key=f"{output_key}:{version}",
            )
        )
    service = ReviewQueryService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        storage=FakeStorage(),
    )

    rows = await service.sections(ActorContext("user-1"), "project-1", run.run_id)

    assert [(row.output_key, row.version) for row in rows] == [
        ("section:methods", 2),
        ("section:results", 1),
    ]


async def test_sections_rejects_wrong_owner_project_and_non_review_run() -> None:
    reviews = FakeReviewRepository()
    run = create_run("project-1", "user-1", RunType.INGESTION)
    reviews.runs[run.run_id] = run
    reviews.review_runs[run.run_id] = create_review_run(
        run_id=run.run_id,
        research_question="研究问题",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={"section": "section_draft.v1"},
        config_snapshot={"source_limit": 10},
    )
    reviews.authorize_run(run.run_id, "project-1", "user-1")
    runs = FakeRunRepository()
    await runs.add(run)
    service = ReviewQueryService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        storage=FakeStorage(),
    )

    for actor, project_id in (
        (ActorContext("user-2"), "project-1"),
        (ActorContext("user-1"), "project-2"),
        (ActorContext("user-1"), "project-1"),
    ):
        with pytest.raises(RunNotFoundError):
            await service.sections(actor, project_id, run.run_id)
