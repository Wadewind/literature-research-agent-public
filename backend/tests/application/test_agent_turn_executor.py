"""AgentTurnExecutor 的事务分段行为测试。"""

from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from literature_agent.application.agent_turn_executor import AgentTurnExecutor
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import ExecutionOutcome, RunExecutionService
from literature_agent.domain.run import RunType
from literature_agent.infrastructure.agent.fake_research_agent_runtime import (
    FakeResearchAgentRuntime,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine


class _TrackedFactory:
    def __init__(self, factory) -> None:
        self.factory = factory
        self.active = 0
        self.closed_transactions = 0

    @asynccontextmanager
    async def __call__(self):
        async with self.factory() as session:
            self.active += 1
            try:
                yield session
            finally:
                self.active -= 1
                self.closed_transactions += 1


class _AssertingRuntime(FakeResearchAgentRuntime):
    def __init__(self, tracked: _TrackedFactory) -> None:
        super().__init__()
        self.tracked = tracked
        self.calls: list[tuple[str, int]] = []

    def execute_turn(self, request):
        self.calls.append(("execute", self.tracked.closed_transactions))
        assert self.tracked.active == 0
        return super().execute_turn(request)

    async def reconcile_turn(self, turn_run_id):
        self.calls.append(("reconcile", self.tracked.closed_transactions))
        assert self.tracked.active == 0
        return await super().reconcile_turn(turn_run_id)

    async def collect_turn_result(self, turn_run_id):
        self.calls.append(("collect", self.tracked.closed_transactions))
        assert self.tracked.active == 0
        result = await super().collect_turn_result(turn_run_id)
        candidate = result.artifact_candidates[0]
        return replace(
            result,
            artifact_candidates=(
                candidate,
                replace(candidate, candidate_id=f"{candidate.candidate_id}-alias"),
            ),
        )


@pytest.mark.asyncio
async def test_executor_calls_runtime_after_read_transaction_and_commits_result_separately(
    db_engine,
) -> None:
    """三个 Runtime 操作均在事务外，结果由后续短事务统一提交。"""
    scenario = await seed_agent_scenario(db_engine)
    tracked = _TrackedFactory(scenario.factory)
    service = make_agent_service(tracked)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="执行一轮",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-turn-1",
        correlation_id="executor-submit",
    )
    before_execution = tracked.closed_transactions
    runtime = _AssertingRuntime(tracked)
    executor = AgentTurnExecutor(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
    )
    dispatcher = RunDispatcher(
        tracked,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        {RunType.AGENT_TURN: executor.execute},
    )
    runner = RunExecutionService(
        tracked,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        SqlalchemyAttemptRepository,
        SqlalchemyOutboxRepository,
        dispatcher.execute,
        "application-worker",
        heartbeat_interval_seconds=3600,
    )

    assert await runner.execute(submitted.run_id, "executor-worker") is ExecutionOutcome.COMPLETED
    assert [name for name, _closed in runtime.calls] == ["execute", "reconcile", "collect"]
    assert all(closed > before_execution for _name, closed in runtime.calls)
    view = await service.get_turn(scenario.actor, submitted.run_id)
    messages = await service.list_messages(scenario.actor, agent_session.session_id)
    assert view.run.status.value == "succeeded"
    assert [message.role.value for message in messages] == ["user", "assistant"]
    assert len(view.candidates) == 1
    async with scenario.factory() as session:
        events = await SqlalchemyEventRepository(session).list_by_run(submitted.run_id)
    assert [event.event_type for event in events].count("agent_artifact_staged") == 1
    succeeded = next(event for event in events if event.event_type == "agent_turn_succeeded")
    assert succeeded.payload["candidate_count"] == 1
