"""真实 PostgreSQL Checkpoint 的跨 Runtime 崩溃恢复测试。"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.review_step_service import ReviewStepService
from literature_agent.domain.project import create_project
from literature_agent.domain.review import (
    ReviewStepKey,
    create_review_run,
)
from literature_agent.domain.run import create_run
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from literature_agent.infrastructure.workflow.postgres_checkpoint import (
    PostgresCheckpointStore,
)
from literature_agent.workflows.review_graph import (
    ReviewGraphFactory,
    ReviewGraphState,
    ReviewWorkflowRuntime,
)


def _state(run_id: str) -> ReviewGraphState:
    return ReviewGraphState(
        review_run_id=run_id,
        project_id="project-1",
        workflow_version="review.v1",
        research_question="如何验证持久恢复？",
    )


async def test_checkpoint_recovers_across_connections_and_isolates_threads(db_engine) -> None:
    """两个全新连接/Runtime 共享 PostgreSQL 历史，Run 之间互不串状态。"""
    database_url = db_engine.url.render_as_string(hide_password=False)
    store = PostgresCheckpointStore(database_url)
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run_ids: list[str] = []
    async with session_factory() as session:
        project = create_project("user-1", "Checkpoint 测试", "")
        await SqlalchemyProjectRepository(session).add(project)
        await session.flush()
        for _ in range(2):
            run = create_run(project.project_id, "user-1", "review")
            run_ids.append(run.run_id)
            await SqlalchemyRunRepository(session).add(run)
            await session.flush()
            await SqlalchemyReviewRepository(session).add_review_run(
                create_review_run(
                    run_id=run.run_id,
                    research_question="如何验证持久恢复？",
                    workflow_version="review.v1",
                    model_profile_version="review-default.v1",
                    prompt_versions={"search_strategy": "search_strategy.v1"},
                    config_snapshot={},
                )
            )
        await session.commit()
    step_service = ReviewStepService(session_factory, SqlalchemyReviewRepository)
    first_run_id, second_run_id = run_ids
    calls: dict[str, int] = {}

    async def node(state: ReviewGraphState) -> dict:
        run_id = state["review_run_id"]
        calls[run_id] = calls.get(run_id, 0) + 1
        step = await step_service.ensure_step(
            run_id=run_id,
            project_id=project.project_id,
            owner_id="user-1",
            step_key=ReviewStepKey.FORMULATE_SEARCH_STRATEGY,
            sequence=2,
            idempotency_key=f"{run_id}:formulate-search:v1",
            input_refs={"workflow_version": "review.v1"},
        )
        if run_id == first_run_id and calls[run_id] == 1:
            raise RuntimeError("模拟副作用提交后 Worker 崩溃")
        return {"search_strategy_output_id": step.step_id}

    # 仅集成测试在 create_all fixture 后用官方 setup 准备表；生产由 Alembic 迁移，
    # PostgresCheckpointStore 本身从不调用 setup。
    async with store.open() as bootstrap:
        assert isinstance(bootstrap, AsyncPostgresSaver)
        await bootstrap.setup()
        first = ReviewWorkflowRuntime(ReviewGraphFactory(node), bootstrap)
        try:
            await first.start(_state(first_run_id))
        except RuntimeError:
            pass
        else:  # pragma: no cover - 防止故障注入被意外删除
            raise AssertionError("首次执行应模拟崩溃")

    async with store.open() as second_saver:
        second = ReviewWorkflowRuntime(ReviewGraphFactory(node), second_saver)
        resumed = await second.resume(first_run_id)
        isolated = await second.start(_state(second_run_id))
        repeated = await second.resume(first_run_id)

    assert resumed["search_strategy_output_id"] == repeated["search_strategy_output_id"]
    assert isolated["search_strategy_output_id"] != resumed["search_strategy_output_id"]
    assert calls == {first_run_id: 2, second_run_id: 1}
    async with session_factory() as session:
        repository = SqlalchemyReviewRepository(session)
        run_one_steps = await repository.list_steps_scoped(
            first_run_id, project.project_id, "user-1"
        )
        run_two_steps = await repository.list_steps_scoped(
            second_run_id, project.project_id, "user-1"
        )
    assert len(run_one_steps) == 1
    assert len(run_two_steps) == 1
    async with db_engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT DISTINCT thread_id, checkpoint_ns FROM checkpoints ORDER BY thread_id")
            )
        ).all()
    assert rows == sorted(
        [
            (f"review.v1:review-run:{first_run_id}", ""),
            (f"review.v1:review-run:{second_run_id}", ""),
        ]
    )
