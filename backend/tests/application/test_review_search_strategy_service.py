"""固定搜索策略生成服务测试。"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace

import pytest

from literature_agent.application.review_search_strategy_service import (
    ReviewSearchStrategyService,
)
from literature_agent.domain.exceptions import RunNotFoundError
from literature_agent.domain.model_types import ChatResult, ModelUsage
from literature_agent.domain.review import ReviewStage, create_review_run
from literature_agent.domain.review_search_strategy import SearchStrategyValidationError
from literature_agent.domain.run import RunStatus, RunType, create_run
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository


class _Model:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0
        self.after_generate: Callable[[], Awaitable[None]] | None = None

    async def generate(self, *_args, **_kwargs):
        self.calls += 1
        if self.after_generate is not None:
            await self.after_generate()
        return ChatResult(self.content, "fake", ModelUsage(10, 5))


async def _service(content: str):
    run_repo = FakeRunRepository()
    review_repo = FakeReviewRepository()
    event_repo = FakeEventRepository()
    model = _Model(content)
    run = replace(
        create_run("project-1", "user-1", RunType.REVIEW),
        run_id="review-1",
        status=RunStatus.RUNNING,
    )
    await run_repo.add(run)
    review_repo.authorize_run("review-1", "project-1", "user-1")
    await review_repo.add_review_run(
        create_review_run(
            run_id="review-1",
            research_question="Agent 如何可靠恢复？",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"search_strategy": "search_strategy.v1"},
            config_snapshot={"source_limit": 10},
        )
    )
    service = ReviewSearchStrategyService(
        session_factory=fake_session,
        run_repo_factory=lambda _: run_repo,
        review_repo_factory=lambda _: review_repo,
        event_repo_factory=lambda _: event_repo,
        model_gateway=model,
    )
    return service, model, review_repo, event_repo, run_repo


def _valid() -> str:
    return json.dumps(
        {
            "normalized_question": "Agent 如何可靠恢复？",
            "arxiv_query": 'all:"agent recovery"',
            "dimensions": [
                {
                    "dimension_key": "method",
                    "name": "方法",
                    "extraction_question": "使用什么方法？",
                },
                {
                    "dimension_key": "evaluation",
                    "name": "评测",
                    "extraction_question": "如何评测？",
                },
                {
                    "dimension_key": "limitations",
                    "name": "限制",
                    "extraction_question": "有何限制？",
                },
            ],
        },
        ensure_ascii=False,
    )


async def test_valid_strategy_is_persisted_and_replay_skips_model() -> None:
    service, model, repo, events, _ = await _service(_valid())
    first = await service.formulate(
        run_id="review-1", project_id="project-1", owner_id="user-1", correlation_id="c"
    )
    second = await service.formulate(
        run_id="review-1", project_id="project-1", owner_id="user-1", correlation_id="c"
    )
    assert first.output.output_id == second.output.output_id
    assert (first.model_invocations, second.model_invocations) == (1, 0)
    assert model.calls == 1
    assert repo.steps[0].status.value == "succeeded"
    assert repo.review_runs["review-1"].current_stage is ReviewStage.SEARCH_ARXIV
    assert [item.event_type for item in await events.list_by_run("review-1")] == [
        "search_strategy_completed"
    ]


async def test_invalid_strategy_fails_step_without_repair() -> None:
    service, model, repo, _, _ = await _service('{"normalized_question":"x"}')
    with pytest.raises(SearchStrategyValidationError):
        await service.formulate(
            run_id="review-1", project_id="project-1", owner_id="user-1", correlation_id="c"
        )
    assert model.calls == 1
    assert repo.outputs == []
    assert repo.steps[0].status.value == "failed"


async def test_cancel_after_model_call_persists_no_strategy_effect() -> None:
    service, model, repo, events, runs = await _service(_valid())
    async def cancel_after_generate():
        run = await runs.get_by_id("review-1")
        assert run is not None
        await runs.add(replace(run, status=RunStatus.CANCEL_REQUESTED))

    model.after_generate = cancel_after_generate
    with pytest.raises(RunNotFoundError, match="Run review-1 不存在"):
        await service.formulate(
            run_id="review-1", project_id="project-1", owner_id="user-1", correlation_id="c"
        )
    assert repo.outputs == []
    assert repo.steps == []
    assert await events.list_by_run("review-1") == []
