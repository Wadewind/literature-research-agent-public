"""AgentTurn 的 PostgreSQL 故障注入与 Effectively Once 证据。"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from literature_agent.application.agent_turn_executor import AgentTurnExecutor
from literature_agent.application.agent_turn_lifecycle_service import (
    AgentTurnLifecycleService,
)
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEventKind,
)
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import (
    ExecutionOutcome,
    RunExecutionService,
)
from literature_agent.application.run_reconcile_service import RunReconcileService
from literature_agent.application.run_service import RunService
from literature_agent.domain.run import RunStatus, RunType
from literature_agent.domain.run_attempt import create_run_attempt
from literature_agent.infrastructure.agent.fake_research_agent_runtime import (
    FakeResearchAgentRuntime,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario


class _CancelFailsRuntime(FakeResearchAgentRuntime):
    """流保持 RUNNING，但取消传播返回 temporary 错误。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.stream_closed = asyncio.Event()
        self.cancel_calls = 0
        self.collect_calls = 0

    def execute_turn(self, request):
        return self._blocking_stream(request)

    async def _blocking_stream(self, request):
        try:
            async for event in super().execute_turn(request):
                yield event
                if event.kind is RuntimeEventKind.STARTED:
                    self.started.set()
                    await asyncio.Event().wait()
        finally:
            self.stream_closed.set()

    async def cancel_turn(self, turn_run_id):
        self.cancel_calls += 1
        raise ResearchAgentRuntimeError(
            kind=RuntimeErrorKind.TEMPORARY,
            code="runtime_cancel_temporarily_unavailable",
            safe_message="Runtime 暂时无法确认取消",
        )

    async def collect_turn_result(self, turn_run_id):
        self.collect_calls += 1
        return await super().collect_turn_result(turn_run_id)


def _runner(factory, runtime: FakeResearchAgentRuntime, worker_id: str) -> RunExecutionService:
    executor = AgentTurnExecutor(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        runtime=runtime,
    )
    dispatcher = RunDispatcher(
        factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        {RunType.AGENT_TURN: executor.execute},
    )
    return RunExecutionService(
        factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        SqlalchemyAttemptRepository,
        SqlalchemyOutboxRepository,
        dispatcher.execute,
        worker_id,
        heartbeat_interval_seconds=3600,
    )


@pytest.mark.asyncio
async def test_result_commit_crash_reconciles_runtime_and_commits_business_once(db_engine) -> None:
    """Runtime 成功后的结果 commit 崩溃，重试只收集一次稳定结果。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="结果提交崩溃后恢复",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="result-commit-crash",
        correlation_id="submit",
    )
    async with scenario.factory() as session:
        outbox_repo = SqlalchemyOutboxRepository(session)
        outbox = await outbox_repo.get_by_run_id(submitted.run_id)
        assert outbox is not None
        assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
        await session.commit()

    class FailingResultCommitSession(AsyncSession):
        commits = 0

        async def commit(self) -> None:
            type(self).commits += 1
            if type(self).commits == 2:
                await self.flush()
                raise RuntimeError("模拟 Agent 结果 commit 前进程丢失")
            await super().commit()

    failing_factory = async_sessionmaker(
        db_engine,
        class_=FailingResultCommitSession,
        expire_on_commit=False,
    )
    runtime = FakeResearchAgentRuntime()
    first = await _runner(failing_factory, runtime, "crashing-worker").execute(
        submitted.run_id, "attempt-1"
    )
    assert first is ExecutionOutcome.RETRY_SCHEDULED
    assert runtime.execution_start_count == 1

    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        retrying = await run_repo.get_by_id(submitted.run_id)
        messages = await SqlalchemyAgentRepository(session).list_messages_scoped(
            agent_session.session_id, scenario.actor.owner_id
        )
        candidates = await SqlalchemyAgentRepository(session).list_candidates_scoped(
            submitted.run_id, scenario.actor.owner_id
        )
        assert retrying is not None and retrying.status is RunStatus.RETRY_WAIT
        assert [message.role.value for message in messages] == ["user"]
        assert candidates == []
        assert await run_repo.update_status(
            submitted.run_id,
            RunStatus.RETRY_WAIT,
            RunStatus.QUEUED,
            retrying.event_sequence,
        )
        await session.commit()

    runner = _runner(scenario.factory, runtime, "recovery-worker")
    assert await runner.execute(submitted.run_id, "attempt-2") is ExecutionOutcome.COMPLETED
    assert await runner.execute(submitted.run_id, "ack-replay") is ExecutionOutcome.SKIPPED
    assert runtime.execution_start_count == 1

    async with scenario.factory() as session:
        agent_repo = SqlalchemyAgentRepository(session)
        messages = await agent_repo.list_messages_scoped(
            agent_session.session_id, scenario.actor.owner_id
        )
        candidates = await agent_repo.list_candidates_scoped(
            submitted.run_id, scenario.actor.owner_id
        )
        events = await SqlalchemyEventRepository(session).list_by_run(submitted.run_id)
        attempts = await SqlalchemyAttemptRepository(session).list_by_run(submitted.run_id)
    assert [message.role.value for message in messages] == ["user", "assistant"]
    assert len(candidates) == 1
    assert len(attempts) == 2
    assert [event.event_type for event in events].count("agent_turn_succeeded") == 1
    assert [event.event_type for event in events].count("agent_artifact_staged") == 1
    persisted_payloads = json.dumps(
        [event.payload for event in events], ensure_ascii=False
    )
    assert "结果提交崩溃后恢复" not in persisted_payloads
    assert "Fake Research Agent 确定性响应" not in persisted_payloads


@pytest.mark.asyncio
async def test_cancel_requested_worker_crash_reconcile_releases_agent_session(db_engine) -> None:
    """取消传播期间 Worker 崩溃后，lease 对账收敛 Run/Attempt/Session。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="取消传播时崩溃",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="cancel-crash-reconcile",
        correlation_id="submit",
    )
    now = datetime.now(UTC)
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        queued = await run_repo.get_by_id(submitted.run_id)
        assert queued is not None
        assert await run_repo.update_status(
            submitted.run_id,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            queued.event_sequence,
        )
        attempt = replace(
            create_run_attempt(submitted.run_id, 1, "crashed-worker"),
            started_at=now - timedelta(seconds=120),
            heartbeat_at=now - timedelta(seconds=120),
        )
        await SqlalchemyAttemptRepository(session).add(attempt)
        assert await run_repo.update_status(
            submitted.run_id,
            RunStatus.RUNNING,
            RunStatus.CANCEL_REQUESTED,
            queued.event_sequence,
        )
        await session.commit()

    lifecycle = AgentTurnLifecycleService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyAgentRepository,
    )
    reconciler = RunReconcileService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        SqlalchemyAttemptRepository,
        SqlalchemyOutboxRepository,
        lease_seconds=30,
        max_run_attempts=3,
        terminal_callback=lifecycle.release_if_terminal,
    )
    assert await reconciler.reconcile_expired(now) == 1
    assert await reconciler.reconcile_expired(now + timedelta(seconds=1)) == 0

    view = await service.get_turn(scenario.actor, submitted.run_id)
    loaded_session = await service.get_session(scenario.actor, agent_session.session_id)
    async with scenario.factory() as session:
        stored_attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(
            submitted.run_id
        )
        events = await SqlalchemyEventRepository(session).list_by_run(submitted.run_id)
    assert view.run.status is RunStatus.CANCELLED
    assert loaded_session.active_turn_run_id is None
    assert stored_attempt is not None and stored_attempt.status.value == "cancelled"
    assert [event.event_type for event in events].count("run_cancelled") == 1


@pytest.mark.asyncio
async def test_cancel_runtime_temporary_failure_stops_heartbeat_then_reconciles(
    db_engine,
) -> None:
    """取消传播失败不转失败/重试；停止心跳后由 lease 对账取消。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="取消传播暂时失败",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="cancel-runtime-temporary-failure",
        correlation_id="submit",
    )
    runtime = _CancelFailsRuntime()
    executor = AgentTurnExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        runtime=runtime,
        cancellation_poll_interval_seconds=0,
    )
    dispatcher = RunDispatcher(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        {RunType.AGENT_TURN: executor.execute},
    )
    runner = RunExecutionService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        SqlalchemyAttemptRepository,
        SqlalchemyOutboxRepository,
        dispatcher.execute,
        "cancel-failing-worker",
        heartbeat_interval_seconds=0.01,
    )
    execution = asyncio.create_task(runner.execute(submitted.run_id, "worker"))
    await asyncio.wait_for(runtime.started.wait(), timeout=5)
    requested = await RunService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
    ).cancel_run(scenario.actor, submitted.run_id, "user-cancel")
    assert requested.status is RunStatus.CANCEL_REQUESTED

    assert await asyncio.wait_for(execution, timeout=5) is ExecutionOutcome.SKIPPED
    assert runtime.cancel_calls == 1
    assert runtime.collect_calls == 0
    assert runtime.stream_closed.is_set()
    view = await service.get_turn(scenario.actor, submitted.run_id)
    messages = await service.list_messages(scenario.actor, agent_session.session_id)
    async with scenario.factory() as session:
        attempt_before = await SqlalchemyAttemptRepository(session).get_latest_by_run(
            submitted.run_id
        )
    assert view.run.status is RunStatus.CANCEL_REQUESTED
    assert [message.role.value for message in messages] == ["user"]
    assert view.candidates == ()
    assert attempt_before is not None and attempt_before.status.value == "running"

    await asyncio.sleep(0.05)
    async with scenario.factory() as session:
        attempt_after = await SqlalchemyAttemptRepository(session).get_latest_by_run(
            submitted.run_id
        )
    assert attempt_after is not None
    assert attempt_after.heartbeat_at == attempt_before.heartbeat_at

    lifecycle = AgentTurnLifecycleService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyAgentRepository,
    )
    reconciler = RunReconcileService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        SqlalchemyAttemptRepository,
        SqlalchemyOutboxRepository,
        lease_seconds=1,
        max_run_attempts=3,
        terminal_callback=lifecycle.release_if_terminal,
    )
    reconcile_now = attempt_after.heartbeat_at + timedelta(seconds=2)
    assert await reconciler.reconcile_expired(reconcile_now) == 1

    final_view = await service.get_turn(scenario.actor, submitted.run_id)
    loaded_session = await service.get_session(scenario.actor, agent_session.session_id)
    async with scenario.factory() as session:
        final_attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(
            submitted.run_id
        )
        events = await SqlalchemyEventRepository(session).list_by_run(submitted.run_id)
    assert final_view.run.status is RunStatus.CANCELLED
    assert loaded_session.active_turn_run_id is None
    assert final_attempt is not None and final_attempt.status.value == "cancelled"
    assert [event.event_type for event in events].count("run_cancelled") == 1
    assert not any(event.event_type == "run_failed" for event in events)
    assert not any(event.event_type == "run_retry_scheduled" for event in events)
