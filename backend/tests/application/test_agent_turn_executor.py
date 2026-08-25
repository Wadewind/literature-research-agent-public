"""AgentTurnExecutor 的事务分段与恢复行为测试。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from literature_agent.application.agent_turn_executor import AgentTurnExecutor
from literature_agent.application.agent_turn_lifecycle_service import (
    AgentTurnLifecycleService,
)
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEventKind,
    RuntimeExecutionState,
    RuntimeTurnReconciliation,
)
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import ExecutionOutcome, RunExecutionService
from literature_agent.application.run_service import RunService
from literature_agent.domain.research_agent import RuntimeSessionBinding, RuntimeTurnBinding
from literature_agent.domain.run import RunStatus, RunType
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


class _ResponseLostAfterSuccessRuntime(FakeResearchAgentRuntime):
    """首次完成后模拟 Runtime 成功响应在业务提交前丢失。"""

    def __init__(self) -> None:
        super().__init__()
        self.execute_calls = 0
        self._lose_succeeded_reconciliation = True

    def execute_turn(self, request):
        self.execute_calls += 1
        return super().execute_turn(request)

    async def reconcile_turn(self, turn_run_id):
        reconciliation = await super().reconcile_turn(turn_run_id)
        if (
            reconciliation.state is RuntimeExecutionState.SUCCEEDED
            and self._lose_succeeded_reconciliation
        ):
            self._lose_succeeded_reconciliation = False
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.TEMPORARY,
                code="runtime_response_lost",
                safe_message="Runtime 成功响应丢失",
            )
        return reconciliation


class _BlockingRuntime(FakeResearchAgentRuntime):
    """在 STARTED 后阻塞，用显式事件控制运行中取消竞争。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
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
                    await self.release.wait()
        finally:
            self.stream_closed.set()

    async def cancel_turn(self, turn_run_id):
        self.cancel_calls += 1
        return await super().cancel_turn(turn_run_id)

    async def collect_turn_result(self, turn_run_id):
        self.collect_calls += 1
        return await super().collect_turn_result(turn_run_id)


class _PermanentRuntimeFailure(FakeResearchAgentRuntime):
    """在对账入口返回稳定永久错误，不允许启动 Execution。"""

    def __init__(self) -> None:
        super().__init__()
        self.execute_calls = 0

    async def reconcile_turn(self, turn_run_id):
        raise ResearchAgentRuntimeError(
            kind=RuntimeErrorKind.PERMANENT,
            code="runtime_policy_rejected",
            safe_message="Runtime 策略拒绝",
        )

    def execute_turn(self, request):
        self.execute_calls += 1
        return super().execute_turn(request)


class _ExistingRunningRuntime(FakeResearchAgentRuntime):
    """模拟另一个 Worker 已建立且仍在运行的同一逻辑 Execution。"""

    def __init__(self, session_id: str, turn_run_id: str) -> None:
        super().__init__()
        self.execute_calls = 0
        self._reconciliation = RuntimeTurnReconciliation(
            turn_run_id=turn_run_id,
            state=RuntimeExecutionState.RUNNING,
            session_binding=RuntimeSessionBinding(
                session_id=session_id,
                binding_id="existing-binding",
                generation=1,
                runtime_thread_id="existing-thread",
                runtime_workspace_id="existing-workspace",
            ),
            turn_binding=RuntimeTurnBinding(
                session_id=session_id,
                turn_run_id=turn_run_id,
                session_binding_id="existing-binding",
                runtime_execution_id="existing-execution",
                runtime_checkpoint_id="existing-checkpoint",
            ),
            last_event_sequence=2,
            result_available=False,
        )

    async def reconcile_turn(self, turn_run_id):
        assert turn_run_id == self._reconciliation.turn_run_id
        return self._reconciliation

    def execute_turn(self, request):
        self.execute_calls += 1
        return super().execute_turn(request)


class _ControlledStatusExecutor(AgentTurnExecutor):
    """用可观察 watcher 固定子任务清理行为。"""

    def __init__(self, *args, runtime_started: asyncio.Event, fail_watch: bool, **kwargs):
        super().__init__(*args, **kwargs)
        self._runtime_started = runtime_started
        self._fail_watch = fail_watch
        self.watcher_closed = asyncio.Event()
        self.release_watcher = asyncio.Event()

    async def _wait_until_run_leaves_running(self, run_id, owner_id):
        del run_id, owner_id
        try:
            await self._runtime_started.wait()
            if self._fail_watch:
                raise RuntimeError("模拟取消状态观察失败")
            await self.release_watcher.wait()
            return RunStatus.RUNNING
        finally:
            self.watcher_closed.set()


class _MismatchedRuntime(FakeResearchAgentRuntime):
    """完成执行后返回指定作用域错配，验证平台拒绝脏 Binding/结果。"""

    def __init__(self, mismatch: str) -> None:
        super().__init__()
        self._mismatch = mismatch
        self.collect_calls = 0

    async def reconcile_turn(self, turn_run_id):
        reconciliation = await super().reconcile_turn(turn_run_id)
        if reconciliation.state is not RuntimeExecutionState.SUCCEEDED:
            return reconciliation
        if self._mismatch == "reconciliation_turn_run_id":
            return replace(reconciliation, turn_run_id="wrong-turn")
        if self._mismatch == "session_binding_session_id":
            binding = replace(reconciliation.session_binding, session_id="wrong-session")
            return replace(reconciliation, session_binding=binding)
        if self._mismatch == "turn_binding_session_id":
            binding = replace(reconciliation.turn_binding, session_id="wrong-session")
            return replace(reconciliation, turn_binding=binding)
        if self._mismatch == "turn_binding_turn_run_id":
            binding = replace(reconciliation.turn_binding, turn_run_id="wrong-turn")
            return replace(reconciliation, turn_binding=binding)
        if self._mismatch == "turn_binding_session_binding_id":
            binding = replace(
                reconciliation.turn_binding,
                session_binding_id="wrong-binding",
            )
            return replace(reconciliation, turn_binding=binding)
        return reconciliation

    async def collect_turn_result(self, turn_run_id):
        self.collect_calls += 1
        result = await super().collect_turn_result(turn_run_id)
        if self._mismatch == "result_turn_run_id":
            return replace(result, turn_run_id="wrong-turn")
        return result


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
    assert [name for name, _closed in runtime.calls] == [
        "reconcile",
        "execute",
        "reconcile",
        "collect",
    ]
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


@pytest.mark.asyncio
async def test_runtime_success_response_loss_reconciles_without_second_execute(db_engine) -> None:
    """新 Attempt 应先收集既有成功结果，不能再次追加同一 Turn 输入。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="响应丢失后恢复",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-response-loss",
        correlation_id="executor-submit",
    )
    runtime = _ResponseLostAfterSuccessRuntime()
    async with scenario.factory() as session:
        outbox_repo = SqlalchemyOutboxRepository(session)
        outbox = await outbox_repo.get_by_run_id(submitted.run_id)
        assert outbox is not None
        assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
        await session.commit()
    executor = AgentTurnExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
    )

    assert (
        await runner.execute(submitted.run_id, "executor-worker-1")
        is ExecutionOutcome.RETRY_SCHEDULED
    )
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        retrying = await run_repo.get_by_id(submitted.run_id)
        assert retrying is not None and retrying.status.value == "retry_wait"
        assert await run_repo.update_status(
            submitted.run_id,
            retrying.status,
            RunStatus.QUEUED,
            retrying.event_sequence,
        )
        await session.commit()

    assert (
        await runner.execute(submitted.run_id, "executor-worker-2")
        is ExecutionOutcome.COMPLETED
    )
    assert (
        await runner.execute(submitted.run_id, "executor-worker-ack-replay")
        is ExecutionOutcome.SKIPPED
    )
    assert runtime.execute_calls == 1
    assert runtime.execution_start_count == 1
    messages = await service.list_messages(scenario.actor, agent_session.session_id)
    view = await service.get_turn(scenario.actor, submitted.run_id)
    assert [message.role.value for message in messages] == ["user", "assistant"]
    assert len(view.candidates) == 1
    async with scenario.factory() as session:
        events = await SqlalchemyEventRepository(session).list_by_run(submitted.run_id)
        attempts = await SqlalchemyAttemptRepository(session).list_by_run(submitted.run_id)
    assert len(attempts) == 2
    assert [event.event_type for event in events].count("agent_turn_succeeded") == 1
    assert [event.event_type for event in events].count("agent_artifact_staged") == 1


@pytest.mark.asyncio
async def test_running_cancel_propagates_to_runtime_and_commits_no_result(db_engine) -> None:
    """RUNNING 取消必须停止 Runtime，并让业务取消赢过结果提交。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="运行中取消",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-running-cancel",
        correlation_id="executor-submit",
    )
    runtime = _BlockingRuntime()
    executor = AgentTurnExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
    )
    task = asyncio.create_task(runner.execute(submitted.run_id, "executor-worker"))
    await asyncio.wait_for(runtime.started.wait(), timeout=5)
    cancel_service = RunService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
    )
    requested = await cancel_service.cancel_run(
        scenario.actor, submitted.run_id, "user-cancel"
    )
    assert requested.status is RunStatus.CANCEL_REQUESTED

    assert await asyncio.wait_for(task, timeout=5) is ExecutionOutcome.SKIPPED
    assert runtime.cancel_calls == 1
    assert runtime.collect_calls == 0
    view = await service.get_turn(scenario.actor, submitted.run_id)
    messages = await service.list_messages(scenario.actor, agent_session.session_id)
    loaded_session = await service.get_session(scenario.actor, agent_session.session_id)
    assert view.run.status is RunStatus.CANCELLED
    assert [message.role.value for message in messages] == ["user"]
    assert view.candidates == ()
    assert loaded_session.active_turn_run_id is None
    async with scenario.factory() as session:
        events = await SqlalchemyEventRepository(session).list_by_run(submitted.run_id)
        attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(submitted.run_id)
    assert [event.event_type for event in events][-3:] == [
        "run_cancel_requested",
        "run_cancelled",
        "agent_turn_cancelled",
    ]
    assert attempt is not None and attempt.status.value == "cancelled"

    following = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="取消后的下一轮",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-after-cancel",
        correlation_id="executor-following",
    )
    assert following.status == "queued"


@pytest.mark.asyncio
async def test_queued_cancel_releases_active_turn_without_starting_runtime(db_engine) -> None:
    """QUEUED 取消应立即释放 Session，且重复 Job 不触碰 Runtime。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="排队时取消",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-queued-cancel",
        correlation_id="executor-submit",
    )
    lifecycle = AgentTurnLifecycleService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyAgentRepository,
    )
    cancel_service = RunService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        terminal_callback=lifecycle.release_if_terminal,
    )
    cancelled = await cancel_service.cancel_run(
        scenario.actor, submitted.run_id, "queued-cancel"
    )
    assert cancelled.status is RunStatus.CANCELLED
    loaded_session = await service.get_session(scenario.actor, agent_session.session_id)
    assert loaded_session.active_turn_run_id is None

    runtime = FakeResearchAgentRuntime()
    executor = AgentTurnExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
        terminal_callback=lifecycle.release_if_terminal,
    )
    assert await runner.execute(submitted.run_id, "duplicate-job") is ExecutionOutcome.SKIPPED
    assert runtime.execution_start_count == 0


@pytest.mark.asyncio
async def test_permanent_runtime_failure_fails_once_and_releases_session(db_engine) -> None:
    """Runtime permanent 错误不占重试预算，并释放失败 Turn。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="永久错误",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-permanent-failure",
        correlation_id="executor-submit",
    )
    async with scenario.factory() as session:
        outbox_repo = SqlalchemyOutboxRepository(session)
        outbox = await outbox_repo.get_by_run_id(submitted.run_id)
        assert outbox is not None
        assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
        await session.commit()
    lifecycle = AgentTurnLifecycleService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyAgentRepository,
    )
    runtime = _PermanentRuntimeFailure()
    executor = AgentTurnExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
        terminal_callback=lifecycle.release_if_terminal,
    )

    assert await runner.execute(submitted.run_id, "executor-worker") is ExecutionOutcome.FAILED
    assert runtime.execute_calls == 0
    view = await service.get_turn(scenario.actor, submitted.run_id)
    loaded_session = await service.get_session(scenario.actor, agent_session.session_id)
    assert view.run.status is RunStatus.FAILED
    assert loaded_session.active_turn_run_id is None
    async with scenario.factory() as session:
        attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(submitted.run_id)
    assert attempt is not None
    assert attempt.error == {
        "type": "runtime_policy_rejected",
        "message": "Runtime 策略拒绝",
    }


@pytest.mark.asyncio
async def test_existing_running_execution_retries_without_appending_input(db_engine) -> None:
    """已有 RUNNING Execution 只继续对账，不调用 execute_turn 追加输入。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="已有运行中的 Execution",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-existing-running",
        correlation_id="executor-submit",
    )
    async with scenario.factory() as session:
        outbox_repo = SqlalchemyOutboxRepository(session)
        outbox = await outbox_repo.get_by_run_id(submitted.run_id)
        assert outbox is not None
        assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
        await session.commit()
    runtime = _ExistingRunningRuntime(agent_session.session_id, submitted.run_id)
    executor = AgentTurnExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
    )

    assert (
        await runner.execute(submitted.run_id, "executor-worker")
        is ExecutionOutcome.RETRY_SCHEDULED
    )
    assert runtime.execute_calls == 0
    view = await service.get_turn(scenario.actor, submitted.run_id)
    loaded_session = await service.get_session(scenario.actor, agent_session.session_id)
    assert view.run.status is RunStatus.RETRY_WAIT
    assert loaded_session.active_turn_run_id == submitted.run_id
    async with scenario.factory() as session:
        attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(submitted.run_id)
    assert attempt is not None
    assert attempt.error == {
        "type": "runtime_turn_still_running",
        "message": "Runtime Turn 仍在执行，稍后继续对账",
    }


@pytest.mark.asyncio
async def test_status_watch_failure_cancels_and_awaits_runtime_stream(db_engine) -> None:
    """状态观察异常必须终止仍在消费的 Runtime 流，再交给失败策略。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="状态观察异常",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-status-watch-failure",
        correlation_id="executor-submit",
    )
    async with scenario.factory() as session:
        outbox_repo = SqlalchemyOutboxRepository(session)
        outbox = await outbox_repo.get_by_run_id(submitted.run_id)
        assert outbox is not None
        assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
        await session.commit()
    runtime = _BlockingRuntime()
    executor = _ControlledStatusExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
        runtime_started=runtime.started,
        fail_watch=True,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
    )

    try:
        assert (
            await runner.execute(submitted.run_id, "status-watch-failure")
            is ExecutionOutcome.RETRY_SCHEDULED
        )
        assert executor.watcher_closed.is_set()
        assert runtime.stream_closed.is_set()
        async with scenario.factory() as session:
            attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(
                submitted.run_id
            )
        assert attempt is not None
        assert attempt.error == {
            "type": "RuntimeError",
            "message": "模拟取消状态观察失败",
        }
    finally:
        runtime.release.set()
        executor.release_watcher.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_outer_cancellation_cleans_runtime_stream_and_status_watcher(db_engine) -> None:
    """Worker 外层任务取消不能遗留 Runtime consumer 或状态 watcher。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="外层任务取消",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="executor-outer-cancel",
        correlation_id="executor-submit",
    )
    runtime = _BlockingRuntime()
    executor = _ControlledStatusExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
        runtime_started=runtime.started,
        fail_watch=False,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
    )
    task = asyncio.create_task(runner.execute(submitted.run_id, "outer-cancel"))
    await asyncio.wait_for(runtime.started.wait(), timeout=5)

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(runtime.stream_closed.wait(), timeout=0.2)
        await asyncio.wait_for(executor.watcher_closed.wait(), timeout=0.2)
    finally:
        runtime.release.set()
        executor.release_watcher.set()
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "mismatch",
    [
        "reconciliation_turn_run_id",
        "session_binding_session_id",
        "turn_binding_session_id",
        "turn_binding_turn_run_id",
        "turn_binding_session_binding_id",
        "result_turn_run_id",
    ],
)
@pytest.mark.asyncio
async def test_runtime_scope_mismatch_is_permanent_and_persists_no_result(
    db_engine, mismatch: str
) -> None:
    """任何 Runtime Binding/结果错配都必须在业务提交前安全失败。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="拒绝 Runtime 作用域错配",
        review_output_id=scenario.matrix.output_id,
        idempotency_key=f"executor-scope-mismatch-{mismatch}",
        correlation_id="executor-submit",
    )
    lifecycle = AgentTurnLifecycleService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyAgentRepository,
    )
    runtime = _MismatchedRuntime(mismatch)
    executor = AgentTurnExecutor(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        runtime=runtime,
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
        "application-worker",
        heartbeat_interval_seconds=3600,
        terminal_callback=lifecycle.release_if_terminal,
    )

    assert await runner.execute(submitted.run_id, "scope-mismatch") is ExecutionOutcome.FAILED
    view = await service.get_turn(scenario.actor, submitted.run_id)
    messages = await service.list_messages(scenario.actor, agent_session.session_id)
    loaded_session = await service.get_session(scenario.actor, agent_session.session_id)
    async with scenario.factory() as session:
        attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(
            submitted.run_id
        )
    assert view.run.status is RunStatus.FAILED
    assert [message.role.value for message in messages] == ["user"]
    assert view.candidates == ()
    assert loaded_session.active_turn_run_id is None
    assert runtime.collect_calls == (1 if mismatch == "result_turn_run_id" else 0)
    assert attempt is not None
    assert attempt.error == {
        "type": "runtime_scope_mismatch",
        "message": "Runtime Turn 作用域校验失败",
    }
