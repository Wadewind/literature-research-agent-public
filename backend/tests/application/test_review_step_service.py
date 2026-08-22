"""ReviewStepService 幂等边界测试。"""

import pytest

from literature_agent.application.review_step_service import ReviewStepService
from literature_agent.domain.exceptions import IdempotencyConflictError
from literature_agent.domain.review import ReviewStepKey, create_review_run
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository


def _service(repository: FakeReviewRepository) -> ReviewStepService:
    return ReviewStepService(fake_session, lambda _session: repository)


async def test_replay_returns_same_step() -> None:
    repository = FakeReviewRepository()
    repository.authorize_run("run-1", "project-1", "user-1")
    await repository.add_review_run(
        create_review_run(
            run_id="run-1",
            research_question="测试",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"search": "search.v1"},
            config_snapshot={},
        )
    )
    service = _service(repository)
    arguments = {
        "run_id": "run-1",
        "project_id": "project-1",
        "owner_id": "user-1",
        "step_key": ReviewStepKey.FORMULATE_SEARCH_STRATEGY,
        "sequence": 2,
        "idempotency_key": "run-1:search:v1",
        "input_refs": {"workflow_version": "review.v1"},
    }

    first = await service.ensure_step(**arguments)
    second = await service.ensure_step(**arguments)

    assert second.step_id == first.step_id
    assert len(repository.steps) == 1


async def test_same_key_with_different_semantics_conflicts() -> None:
    repository = FakeReviewRepository()
    repository.authorize_run("run-1", "project-1", "user-1")
    await repository.add_review_run(
        create_review_run(
            run_id="run-1",
            research_question="测试",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"search": "search.v1"},
            config_snapshot={},
        )
    )
    service = _service(repository)
    await service.ensure_step(
        run_id="run-1",
        project_id="project-1",
        owner_id="user-1",
        step_key=ReviewStepKey.FORMULATE_SEARCH_STRATEGY,
        sequence=2,
        idempotency_key="same-key",
        input_refs={"version": "v1"},
    )

    with pytest.raises(IdempotencyConflictError):
        await service.ensure_step(
            run_id="run-1",
            project_id="project-1",
            owner_id="user-1",
            step_key=ReviewStepKey.SEARCH_ARXIV,
            sequence=3,
            idempotency_key="same-key",
            input_refs={"version": "v2"},
        )
