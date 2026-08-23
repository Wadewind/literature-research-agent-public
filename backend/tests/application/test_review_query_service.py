"""ReviewQueryService 列表读路径测试。"""

from literature_agent.application.review_query_service import ReviewQueryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.review import create_review_run
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
