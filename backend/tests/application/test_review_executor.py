"""Review 生产执行器在图外依赖等待边界的测试。"""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from literature_agent import metrics as metrics_module
from literature_agent.application import review_executor as review_executor_module
from literature_agent.application.review_executor import ReviewExecutor
from literature_agent.application.review_search_strategy_service import SearchStrategyResult
from literature_agent.domain.exceptions import CheckpointDataError, NoReviewablePapersError
from literature_agent.domain.retry_policy import is_permanent_error
from literature_agent.domain.review import (
    HumanInputAction,
    ReviewOutputType,
    ReviewStage,
    create_human_input,
    create_human_input_request,
    create_review_output,
    create_review_run,
    create_review_source,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository


class _Strategy:
    def __init__(self, output) -> None:
        self.output = output
        self.calls = 0

    async def formulate(self, **_kwargs):
        self.calls += 1
        return SearchStrategyResult(self.output, 0)


class _Arxiv:
    def __init__(self) -> None:
        self.search_calls = 0
        self.import_calls = 0

    async def search_sources(self, **_kwargs):
        self.search_calls += 1

    async def import_sources(self, **_kwargs):
        self.import_calls += 1


class _DependencyWait:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def pause(self, run_id, project_id, owner_id, correlation_id):
        self.calls.append((run_id, project_id, owner_id, correlation_id))


class _Unexpected:
    def __getattr__(self, name):
        raise AssertionError(f"依赖等待前不得访问 {name}")


class _CheckpointStore:
    def __init__(self) -> None:
        self.saver = InMemorySaver()

    @asynccontextmanager
    async def open(self):
        yield self.saver


async def test_pending_source_pauses_outside_langgraph(monkeypatch) -> None:
    runs = FakeRunRepository()
    reviews = FakeReviewRepository()
    run = replace(
        create_run("project-1", "owner-1", RunType.REVIEW).transition_to(RunStatus.RUNNING),
        run_id="review-1",
    )
    await runs.add(run)
    reviews.authorize_run(run.run_id, run.project_id, run.owner_id)
    await reviews.add_review_run(
        create_review_run(
            run_id=run.run_id,
            research_question="研究问题",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"search_strategy": "search_strategy.v1"},
            config_snapshot={"source_limit": 10},
        )
    )
    await reviews.add_source(
        create_review_source(
            review_run_id=run.run_id,
            arxiv_id="2401.00001",
            arxiv_version="v1",
            rank=1,
            metadata_snapshot={"title": "论文"},
        )
    )
    strategy_output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.SEARCH_STRATEGY,
        output_key="search-strategy",
        version=1,
        schema_version="search-strategy.v1",
        payload={
            "normalized_question": "研究问题",
            "arxiv_query": "all:agent",
            "dimensions": [
                {
                    "dimension_key": key,
                    "name": key,
                    "extraction_question": f"{key} 是什么？",
                }
                for key in ("method", "dataset", "evaluation")
            ],
        },
        idempotency_key="review-1:search-strategy:search_strategy.v1",
    )
    strategy = _Strategy(strategy_output)
    arxiv = _Arxiv()
    wait = _DependencyWait()
    unexpected = _Unexpected()
    executor = ReviewExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        strategy_service=strategy,
        arxiv_service=arxiv,
        dependency_wait_service=wait,
        matrix_service=unexpected,
        outline_service=unexpected,
        outline_decision_service=unexpected,
        section_service=unexpected,
        export_service=unexpected,
        source_selection_service=unexpected,
        checkpoint_store=unexpected,
    )
    recorder = Mock()
    monkeypatch.setattr(review_executor_module, "metrics", recorder)
    monkeypatch.setattr(metrics_module, "metrics", recorder)

    await executor.execute(run, "correlation-1")

    assert strategy.calls == 1
    assert arxiv.search_calls == 1
    assert arxiv.import_calls == 1
    assert wait.calls == [("review-1", "project-1", "owner-1", "correlation-1")]
    assert [call.args for call in recorder.record_review_stage.call_args_list] == [
        (ReviewStage.VALIDATE_REQUEST, "succeeded"),
        (ReviewStage.FORMULATE_SEARCH_STRATEGY, "succeeded"),
        (ReviewStage.SEARCH_ARXIV, "succeeded"),
        (ReviewStage.IMPORT_ARXIV_PAPERS, "succeeded"),
        (ReviewStage.WAIT_FOR_INGESTION, "succeeded"),
    ]


async def test_review_v2_retries_source_pause_before_import() -> None:
    runs = FakeRunRepository()
    reviews = FakeReviewRepository()
    run = replace(
        create_run("project-1", "owner-1", RunType.REVIEW).transition_to(RunStatus.RUNNING),
        run_id="review-v2",
    )
    await runs.add(run)
    reviews.authorize_run(run.run_id, run.project_id, run.owner_id)
    review = replace(
        create_review_run(
            run_id=run.run_id,
            research_question="研究问题",
            workflow_version="review.v2",
            model_profile_version="review-default.v3",
            prompt_versions={"search_strategy": "search_strategy.v1"},
            config_snapshot={
                "source_limit": 3,
                "candidate_limit": 10,
                "auto_search_candidates": True,
            },
        ),
        current_stage=ReviewStage.SEARCH_ARXIV,
    )
    await reviews.add_review_run(review)
    strategy_output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.SEARCH_STRATEGY,
        output_key="search-strategy",
        version=1,
        schema_version="search-strategy.v1",
        payload={"arxiv_query": "all:agent", "dimensions": []},
        idempotency_key="review-v2:strategy",
    )

    class Arxiv:
        import_calls = 0

        async def search_sources(self, **kwargs):
            assert kwargs["query"].max_results == 10
            assert kwargs["rank_offset"] == 0
            await reviews.add_source(
                create_review_source(
                    review_run_id=run.run_id,
                    arxiv_id="2601.00001",
                    arxiv_version="v1",
                    rank=1,
                    metadata_snapshot={"title": "候选论文"},
                )
            )
            reviews.review_runs[run.run_id] = replace(
                reviews.review_runs[run.run_id],
                current_stage=ReviewStage.IMPORT_ARXIV_PAPERS,
            )

        async def import_sources(self, **_kwargs):
            self.import_calls += 1

    class Selection:
        calls = 0

        async def pause(self, **kwargs):
            self.calls += 1
            assert kwargs["run_id"] == run.run_id

    arxiv = Arxiv()
    selection = Selection()
    unexpected = _Unexpected()
    executor = ReviewExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        strategy_service=_Strategy(strategy_output),
        arxiv_service=arxiv,
        dependency_wait_service=unexpected,
        matrix_service=unexpected,
        outline_service=unexpected,
        outline_decision_service=unexpected,
        section_service=unexpected,
        export_service=unexpected,
        source_selection_service=selection,
        checkpoint_store=unexpected,
    )

    await executor.execute(run, "corr-v2")
    # 检索事实已提交、暂停事务失败后的 Worker 重试仍必须重新进入 HITL，
    # 不能因为阶段已经推进到 import_arxiv_papers 就导入全部候选。
    await executor.execute(run, "corr-v2-retry")

    assert selection.calls == 2
    assert arxiv.import_calls == 0


@pytest.mark.parametrize("source_state", ["failed", "empty"])
async def test_no_reviewable_sources_fail_without_dependency_wait(source_state: str) -> None:
    runs = FakeRunRepository()
    reviews = FakeReviewRepository()
    run = replace(
        create_run("project-1", "owner-1", RunType.REVIEW).transition_to(RunStatus.RUNNING),
        run_id="review-failed",
    )
    await runs.add(run)
    reviews.authorize_run(run.run_id, run.project_id, run.owner_id)
    await reviews.add_review_run(
        create_review_run(
            run_id=run.run_id,
            research_question="研究问题",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"search_strategy": "search_strategy.v1"},
            config_snapshot={"source_limit": 10},
        )
    )
    if source_state == "failed":
        await reviews.add_source(
            create_review_source(
                review_run_id=run.run_id,
                arxiv_id="2401.00001",
                arxiv_version="v1",
                rank=1,
                metadata_snapshot={"title": "论文"},
            ).mark_failed("arxiv_pdf_not_found")
        )
    output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.SEARCH_STRATEGY,
        output_key="search-strategy",
        version=1,
        schema_version="search-strategy.v1",
        payload={
            "normalized_question": "研究问题",
            "arxiv_query": "all:agent",
            "dimensions": [
                {
                    "dimension_key": key,
                    "name": key,
                    "extraction_question": f"{key} 是什么？",
                }
                for key in ("method", "dataset", "evaluation")
            ],
        },
        idempotency_key="review-failed:search-strategy:search_strategy.v1",
    )
    wait = _DependencyWait()
    unexpected = _Unexpected()
    executor = ReviewExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        strategy_service=_Strategy(output),
        arxiv_service=_Arxiv(),
        dependency_wait_service=wait,
        matrix_service=unexpected,
        outline_service=unexpected,
        outline_decision_service=unexpected,
        section_service=unexpected,
        export_service=unexpected,
        source_selection_service=unexpected,
        checkpoint_store=unexpected,
    )

    with pytest.raises(NoReviewablePapersError, match="no_reviewable_papers"):
        await executor.execute(run, "correlation-1")

    assert wait.calls == []


async def test_human_resume_replays_pregraph_services_without_duplicate_external_effects() -> None:
    runs = FakeRunRepository()
    reviews = FakeReviewRepository()
    run = replace(
        create_run("project-1", "owner-1", RunType.REVIEW).transition_to(RunStatus.RUNNING),
        run_id="review-resume",
    )
    await runs.add(run)
    reviews.authorize_run(run.run_id, run.project_id, run.owner_id)
    review = create_review_run(
        run_id=run.run_id,
        research_question="研究问题",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={"search_strategy": "search_strategy.v1"},
        config_snapshot={"source_limit": 10},
    )
    await reviews.add_review_run(review)
    await reviews.add_source(
        create_review_source(
            review_run_id=run.run_id,
            arxiv_id="2401.00001",
            arxiv_version="v1",
            rank=1,
            metadata_snapshot={"title": "论文"},
        ).mark_ready("paper-1", "version-1")
    )
    strategy_output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.SEARCH_STRATEGY,
        output_key="search-strategy",
        version=1,
        schema_version="search-strategy.v1",
        payload={
            "normalized_question": "研究问题",
            "arxiv_query": "all:agent",
            "dimensions": [
                {
                    "dimension_key": key,
                    "name": key,
                    "extraction_question": f"{key} 是什么？",
                }
                for key in ("method", "dataset", "evaluation")
            ],
        },
        idempotency_key="review-resume:search-strategy:search_strategy.v1",
    )
    matrix_output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="evidence-matrix",
        version=1,
        schema_version="evidence-matrix.v1",
        payload={"rows": []},
        idempotency_key="matrix",
    )
    request = create_human_input_request(
        review_run_id=run.run_id,
        request_version=1,
        outline_output_id="outline-1",
        allowed_actions=[HumanInputAction.APPROVE],
    )
    human_input = create_human_input(
        request=request,
        action=HumanInputAction.APPROVE,
        payload={},
        submitted_by="owner-1",
        idempotency_key="input-1",
    )
    await reviews.add_human_input_request(request.resolve(human_input.human_input_id))
    await reviews.add_human_input(human_input)

    class Strategy:
        calls = 0
        external_calls = 0

        async def formulate(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                self.external_calls += 1
            return SearchStrategyResult(strategy_output, int(self.calls == 1))

    class Arxiv:
        search_calls = 0
        search_external_calls = 0
        import_calls = 0
        download_external_calls = 0

        async def search_sources(self, **_kwargs):
            self.search_calls += 1
            if self.search_calls == 1:
                self.search_external_calls += 1

        async def import_sources(self, **_kwargs):
            self.import_calls += 1
            if self.import_calls == 1:
                self.download_external_calls += 1

    class Matrix:
        calls = 0
        external_calls = 0

        async def build(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                self.external_calls += 1
            return SimpleNamespace(output=matrix_output)

    class Outline:
        async def propose_and_pause(self, **_kwargs):
            current = reviews.review_runs[run.run_id]
            reviews.review_runs[run.run_id] = replace(
                current, current_stage=ReviewStage.REVIEW_OUTLINE
            )
            return SimpleNamespace(
                output=SimpleNamespace(output_id="outline-1"),
                request=SimpleNamespace(request_id=request.request_id),
            )

    class Decision:
        async def load(self, **_kwargs):
            return SimpleNamespace(
                action=HumanInputAction.APPROVE,
                human_input_id=human_input.human_input_id,
                approved_outline_output_id="outline-1",
            )

    class Sections:
        async def draft_sections(self, **_kwargs):
            return SimpleNamespace(outputs=[SimpleNamespace(output_id="section-1")])

        async def validate_sections(self, **_kwargs):
            return SimpleNamespace(claim_set=SimpleNamespace(claim_set_id="claims-1"))

        async def consistency_check(self, **_kwargs):
            return SimpleNamespace(output_id="consistency-1")

    class Export:
        finalize_calls = 0

        async def export(self, **_kwargs):
            return SimpleNamespace(
                final_output=SimpleNamespace(output_id="final-1"),
                markdown_artifact=SimpleNamespace(artifact_id="artifact-1"),
            )

        async def finalize(self, **_kwargs):
            self.finalize_calls += 1
            current = await runs.get_by_id(run.run_id)
            assert current is not None
            await runs.add(replace(current, status=RunStatus.SUCCEEDED))

    strategy = Strategy()
    arxiv = Arxiv()
    matrix = Matrix()
    export = Export()
    executor = ReviewExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        strategy_service=strategy,
        arxiv_service=arxiv,
        dependency_wait_service=_DependencyWait(),
        matrix_service=matrix,
        outline_service=Outline(),
        outline_decision_service=Decision(),
        section_service=Sections(),
        export_service=export,
        source_selection_service=_Unexpected(),
        checkpoint_store=_CheckpointStore(),
    )

    await executor.execute(run, "first-attempt")
    assert (await runs.get_by_id(run.run_id)).status is RunStatus.RUNNING
    await executor.execute(run, "resume-attempt")

    assert (strategy.calls, strategy.external_calls) == (2, 1)
    assert (arxiv.search_calls, arxiv.search_external_calls) == (2, 1)
    assert (arxiv.import_calls, arxiv.download_external_calls) == (2, 1)
    assert (matrix.calls, matrix.external_calls) == (2, 1)
    assert export.finalize_calls == 1
    assert (await runs.get_by_id(run.run_id)).status is RunStatus.SUCCEEDED


async def test_corrupt_checkpoint_is_not_replaced_with_new_graph(monkeypatch) -> None:
    """Checkpoint 损坏必须原样永久失败，不能降级为首次启动覆盖历史。"""
    runs = FakeRunRepository()
    reviews = FakeReviewRepository()
    run = replace(
        create_run("project-1", "owner-1", RunType.REVIEW).transition_to(RunStatus.RUNNING),
        run_id="review-corrupt-checkpoint",
    )
    await runs.add(run)
    reviews.authorize_run(run.run_id, run.project_id, run.owner_id)
    await reviews.add_review_run(
        create_review_run(
            run_id=run.run_id,
            research_question="研究问题",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"search_strategy": "search_strategy.v1"},
            config_snapshot={"source_limit": 10},
        )
    )
    await reviews.add_source(
        create_review_source(
            review_run_id=run.run_id,
            arxiv_id="2401.00001",
            arxiv_version="v1",
            rank=1,
            metadata_snapshot={"title": "论文"},
        ).mark_ready("paper-1", "version-1")
    )
    strategy_output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.SEARCH_STRATEGY,
        output_key="search-strategy",
        version=1,
        schema_version="search-strategy.v1",
        payload={"arxiv_query": "all:agent"},
        idempotency_key="strategy-corrupt",
    )
    matrix_output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="evidence-matrix",
        version=1,
        schema_version="evidence-matrix.v1",
        payload={"rows": []},
        idempotency_key="matrix-corrupt",
    )

    class Matrix:
        async def build(self, **_kwargs):
            return SimpleNamespace(output=matrix_output)

    calls = {"start": 0, "resume": 0}
    corrupt = CheckpointDataError("Checkpoint 无法安全读取")

    class CorruptRuntime:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def has_checkpoint(self, _run_id: str) -> bool:
            raise corrupt

        async def start(self, _state) -> None:
            calls["start"] += 1

        async def resume(self, _run_id: str) -> None:
            calls["resume"] += 1

    monkeypatch.setattr(review_executor_module, "ReviewWorkflowRuntime", CorruptRuntime)
    unexpected = _Unexpected()
    executor = ReviewExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _session: runs,
        review_repo_factory=lambda _session: reviews,
        strategy_service=_Strategy(strategy_output),
        arxiv_service=_Arxiv(),
        dependency_wait_service=_DependencyWait(),
        matrix_service=Matrix(),
        outline_service=unexpected,
        outline_decision_service=unexpected,
        section_service=unexpected,
        export_service=unexpected,
        source_selection_service=unexpected,
        checkpoint_store=_CheckpointStore(),
    )

    with pytest.raises(CheckpointDataError) as raised:
        await executor.execute(run, "correlation-1")

    assert raised.value is corrupt
    assert is_permanent_error(raised.value)
    assert calls == {"start": 0, "resume": 0}
