"""RunExecutionService 应用服务测试。"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from literature_agent.application import run_execution_service as execution_module
from literature_agent.application.run_execution_service import (
    ExecutionOutcome,
    RunExecutionService,
)
from literature_agent.domain.arxiv import ArxivError
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import IdempotencyConflictError, InvalidPdfInputError
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import Run, RunStatus, create_run
from literature_agent.domain.run_attempt import AttemptStatus
from literature_agent.observability import get_log_context
from tests.fakes.fake_attempt_repository import FakeAttemptRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


@pytest.fixture
def run_repo() -> FakeRunRepository:
    """提供 Fake Run Repository。"""
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    """提供 Fake Event Repository。"""
    return FakeEventRepository()


@pytest.fixture
def attempt_repo() -> FakeAttemptRepository:
    """提供 Fake Attempt Repository。"""
    return FakeAttemptRepository()


@pytest.fixture
def outbox_repo() -> FakeOutboxRepository:
    """提供 Fake Outbox Repository。"""
    return FakeOutboxRepository()


def _make_service(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
    executor,
    max_run_attempts: int = 3,
) -> RunExecutionService:
    """构建使用 Fake 依赖的 RunExecutionService。"""
    return RunExecutionService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: run_repo,
        event_repo_factory=lambda _session: event_repo,
        attempt_repo_factory=lambda _session: attempt_repo,
        outbox_repo_factory=lambda _session: outbox_repo,
        executor=executor,
        worker_id="test-worker:1",
        heartbeat_interval_seconds=3600.0,  # 测试中不触发真实心跳
        max_run_attempts=max_run_attempts,
    )


async def _seed_dispatched_outbox(
    outbox_repo: FakeOutboxRepository,
    run_id: str,
) -> None:
    """准备一条已投递的 Outbox 记录（模拟派发完成后的状态）。"""
    entry = create_outbox_entry(run_id)
    await outbox_repo.add(entry)
    await outbox_repo.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))


def _completing_executor(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
):
    """模拟自行推进终态的执行器（RUNNING → SUCCEEDED + 事件）。"""

    async def _execute(run: Run, correlation_id: str) -> None:
        loaded = await run_repo.get_by_id(run.run_id)
        assert loaded is not None
        await run_repo.update_status(
            run.run_id, RunStatus.RUNNING, RunStatus.SUCCEEDED, loaded.event_sequence + 1
        )
        await event_repo.add(
            create_event(
                run_id=run.run_id,
                sequence=loaded.event_sequence,
                event_type="result_committed",
                actor_type="system",
                correlation_id=correlation_id,
                payload={},
            )
        )

    return _execute


async def _add_run(
    run_repo: FakeRunRepository,
    status: RunStatus = RunStatus.QUEUED,
) -> Run:
    """创建一个指定状态的 Run 并存入 Fake Repository。"""
    run = create_run(project_id="p-1", owner_id="user-1", run_type="ingestion")
    if status != RunStatus.QUEUED:
        run = Run(
            run_id=run.run_id,
            project_id=run.project_id,
            owner_id=run.owner_id,
            run_type=run.run_type,
            status=status,
            input_payload=run.input_payload,
            result_payload=run.result_payload,
            event_sequence=run.event_sequence,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
    await run_repo.add(run)
    return run


def _event_types(event_repo: FakeEventRepository, run_id: str) -> list[str]:
    """返回指定 Run 的事件类型列表。"""
    return [e.event_type for e in event_repo._events if e.run_id == run_id]


@pytest.mark.parametrize(
    ("outcome", "event"),
    [
        (ExecutionOutcome.COMPLETED, "run_execution_completed"),
        (ExecutionOutcome.FAILED, "run_execution_failed"),
        (ExecutionOutcome.RETRY_SCHEDULED, "run_execution_retry"),
        (ExecutionOutcome.PAUSED, "run_execution_paused"),
        (ExecutionOutcome.SKIPPED, "run_execution_skipped"),
        (ExecutionOutcome.MISSING, "run_execution_skipped"),
    ],
)
def test_each_execution_outcome_has_fixed_log_event(outcome, event, caplog) -> None:
    """每种执行结果都映射到固定、低敏的结构化事件。"""
    caplog.set_level(logging.INFO, logger="literature_agent.application.run_execution_service")

    assert RunExecutionService._log_outcome(outcome, 0.0) is outcome

    record = caplog.records[-1]
    assert record.event == event
    assert record.status == outcome.value
    assert isinstance(record.duration_ms, int)


async def test_execute_queued_run_completes(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """QUEUED 的 Run 应被认领并交给执行器推进到 SUCCEEDED。"""
    run = await _add_run(run_repo)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo,
        _completing_executor(run_repo, event_repo),
    )

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.SUCCEEDED
    assert _event_types(event_repo, run.run_id) == ["run_started", "result_committed"]
    # 认领时创建 Attempt，终态后关闭为 SUCCEEDED
    attempt = await attempt_repo.get_latest_by_run(run.run_id)
    assert attempt is not None
    assert attempt.attempt_number == 1
    assert attempt.worker_id == "test-worker:1"
    assert attempt.status.value == "succeeded"


async def test_execute_binds_persisted_run_and_attempt_context_then_restores(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """执行上下文来自已认领业务事实，并在 heartbeat/执行作用域后恢复。"""
    run = await _add_run(run_repo)
    observed: dict = {}

    async def executor(running: Run, correlation_id: str) -> None:
        observed.update(get_log_context())
        await _completing_executor(run_repo, event_repo)(running, correlation_id)

    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo, executor)

    assert await service.execute(run.run_id, "worker:corr") is ExecutionOutcome.COMPLETED

    attempt = await attempt_repo.get_latest_by_run(run.run_id)
    assert attempt is not None
    assert observed == {
        "correlation_id": "worker:corr",
        "run_id": run.run_id,
        "project_id": "p-1",
        "attempt_id": attempt.attempt_id,
        "run_type": "ingestion",
    }
    assert get_log_context() == {}


async def test_execute_duplicate_job_is_skipped(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
    monkeypatch,
) -> None:
    """重复 Job 不应重复认领或重复执行。"""
    run = await _add_run(run_repo)
    calls: list[str] = []

    async def _tracking_executor(run: Run, correlation_id: str) -> None:
        calls.append(run.run_id)
        await _completing_executor(run_repo, event_repo)(run, correlation_id)

    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _tracking_executor
    )
    recorder = Mock()
    monkeypatch.setattr(execution_module, "metrics", recorder)

    first = await service.execute(run.run_id, correlation_id="job-1")
    second = await service.execute(run.run_id, correlation_id="job-1")
    missing = await service.execute("missing-run", correlation_id="job-2")

    assert first == ExecutionOutcome.COMPLETED
    assert second == ExecutionOutcome.SKIPPED
    assert missing == ExecutionOutcome.MISSING
    assert calls == [run.run_id]
    recorder.record_run_started.assert_called_once_with(run.run_type)
    recorder.record_run_completed.assert_called_once()
    completed_call = recorder.record_run_completed.call_args
    assert completed_call.args[1] == "succeeded"
    assert completed_call.kwargs["attempt_status"] == "succeeded"


async def test_review_finalize_closes_attempt_without_duplicate_succeeded_event(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """Review Executor 已原子提交终态时，外层只关闭 Attempt。"""
    run = await _add_run(run_repo)

    async def finalize_review(running: Run, correlation_id: str) -> None:
        current = await run_repo.get_by_id(running.run_id)
        assert current is not None
        assert await run_repo.update_status(
            running.run_id,
            RunStatus.RUNNING,
            RunStatus.SUCCEEDED,
            current.event_sequence + 1,
        )
        await event_repo.add(
            create_event(
                run_id=running.run_id,
                sequence=current.event_sequence,
                event_type="run_succeeded",
                actor_type="system",
                correlation_id=correlation_id,
                payload={"final_artifact_id": "artifact-1"},
            )
        )

    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, finalize_review
    )

    assert await service.execute(run.run_id, "review-job") is ExecutionOutcome.COMPLETED
    assert await service.execute(run.run_id, "duplicate-job") is ExecutionOutcome.SKIPPED
    assert _event_types(event_repo, run.run_id) == ["run_started", "run_succeeded"]
    attempt = await attempt_repo.get_latest_by_run(run.run_id)
    assert attempt is not None and attempt.status is AttemptStatus.SUCCEEDED


async def test_execute_missing_run(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """Run 不存在时返回 MISSING，不写事件。"""
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo,
        _completing_executor(run_repo, event_repo),
    )

    outcome = await service.execute("no-such-run", correlation_id="job-1")

    assert outcome == ExecutionOutcome.MISSING
    assert event_repo._events == []


async def test_execute_cancelled_run_is_skipped(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """已取消的 Run 不应被执行。"""
    run = await _add_run(run_repo, status=RunStatus.CANCELLED)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo,
        _completing_executor(run_repo, event_repo),
    )

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.SKIPPED
    assert _event_types(event_repo, run.run_id) == []


@pytest.mark.parametrize(
    "waiting_status",
    [RunStatus.WAITING_INPUT, RunStatus.WAITING_DEPENDENCY],
)
async def test_execute_waiting_run_pauses_attempt(
    waiting_status: RunStatus,
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """执行器进入等待状态时返回 PAUSED，并正常关闭当前 Attempt。"""

    async def _pausing_executor(run: Run, _correlation_id: str) -> None:
        loaded = await run_repo.get_by_id(run.run_id)
        assert loaded is not None
        updated = await run_repo.update_status(
            run.run_id,
            RunStatus.RUNNING,
            waiting_status,
            loaded.event_sequence + 1,
        )
        assert updated

    run = await _add_run(run_repo)
    await _seed_dispatched_outbox(outbox_repo, run.run_id)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _pausing_executor
    )

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.PAUSED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == waiting_status
    attempt = await attempt_repo.get_latest_by_run(run.run_id)
    assert attempt is not None
    assert attempt.status == AttemptStatus.PAUSED
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.DISPATCHED


async def test_execute_transient_error_schedules_retry(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """临时错误在预算内推进 RETRY_WAIT 并重置 Outbox 等待重投。"""

    async def _failing_executor(_run: Run, _correlation_id: str) -> None:
        raise ValueError("解析失败")

    run = await _add_run(run_repo)
    await _seed_dispatched_outbox(outbox_repo, run.run_id)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _failing_executor
    )

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.RETRY_SCHEDULED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.RETRY_WAIT
    events = _event_types(event_repo, run.run_id)
    assert events == ["run_started", "run_retry_scheduled"]
    retry_event = [e for e in event_repo._events if e.event_type == "run_retry_scheduled"][0]
    assert retry_event.payload["error"]["type"] == "ValueError"
    assert retry_event.payload["attempt"] == 1
    assert "next_retry_at" in retry_event.payload
    # Outbox 记录被重置为待投递，退避后到期
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.PENDING
    assert entry.attempt_count == 1
    assert entry.scheduled_at > datetime.now(UTC)
    # Attempt 以 FAILED 关闭
    attempt = await attempt_repo.get_latest_by_run(run.run_id)
    assert attempt is not None
    assert attempt.status.value == "failed"


async def test_execute_arxiv_rate_limit_persists_safe_details_and_respects_hint(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """429 使用安全分类和 Retry-After，不按通用 1 秒立即重投。"""

    async def _failing_executor(_run: Run, _correlation_id: str) -> None:
        raise ArxivError(
            "arxiv_search_rate_limited",
            temporary=True,
            http_status=429,
            retry_after_seconds=30.0,
        )

    before = datetime.now(UTC)
    run = await _add_run(run_repo)
    await _seed_dispatched_outbox(outbox_repo, run.run_id)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _failing_executor
    )

    outcome = await service.execute(run.run_id, correlation_id="job-arxiv-429")

    assert outcome is ExecutionOutcome.RETRY_SCHEDULED
    retry_event = next(
        event for event in event_repo._events if event.event_type == "run_retry_scheduled"
    )
    assert retry_event.payload["error"] == {
        "type": "ArxivError",
        "message": "arxiv_search_rate_limited",
        "http_status": 429,
        "retry_after_seconds": 30.0,
    }
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.scheduled_at >= before + timedelta(seconds=30)


async def test_execute_permanent_error_marks_failed(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """永久错误（输入类）不重试，直接 FAILED。"""

    async def _failing_executor(_run: Run, _correlation_id: str) -> None:
        raise InvalidPdfInputError("文件已加密")

    run = await _add_run(run_repo)
    await _seed_dispatched_outbox(outbox_repo, run.run_id)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _failing_executor
    )

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.FAILED
    events = _event_types(event_repo, run.run_id)
    assert events == ["run_started", "run_failed"]
    failed_event = [e for e in event_repo._events if e.event_type == "run_failed"][0]
    assert failed_event.payload["error"]["type"] == "InvalidPdfInputError"
    # 永久错误不重置 Outbox
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.DISPATCHED


async def test_execute_idempotency_conflict_is_not_retried(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """同键不同事实属于确定性冲突，不得重复执行相同失败路径。"""

    async def _failing_executor(_run: Run, _correlation_id: str) -> None:
        raise IdempotencyConflictError("review:source-selection:1")

    run = await _add_run(run_repo)
    await _seed_dispatched_outbox(outbox_repo, run.run_id)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _failing_executor
    )

    outcome = await service.execute(run.run_id, correlation_id="job-idempotency")

    assert outcome is ExecutionOutcome.FAILED
    assert _event_types(event_repo, run.run_id) == ["run_started", "run_failed"]
    failed = next(item for item in event_repo._events if item.event_type == "run_failed")
    assert failed.payload["error"]["type"] == "IdempotencyConflictError"
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None and entry.status is OutboxStatus.DISPATCHED


async def test_execute_retry_budget_exhausted_marks_failed(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """临时错误耗尽重试预算后推进 FAILED。"""

    async def _failing_executor(_run: Run, _correlation_id: str) -> None:
        raise ValueError("一直失败")

    run = await _add_run(run_repo)
    await _seed_dispatched_outbox(outbox_repo, run.run_id)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo,
        _failing_executor, max_run_attempts=2,
    )

    # 第 1 次失败 → RETRY_WAIT；模拟重投后第 2 次失败 → FAILED
    first = await service.execute(run.run_id, correlation_id="job-1")
    assert first == ExecutionOutcome.RETRY_SCHEDULED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    updated = await run_repo.update_status(
        run.run_id, RunStatus.RETRY_WAIT, RunStatus.QUEUED, loaded.event_sequence + 1
    )
    assert updated

    second = await service.execute(run.run_id, correlation_id="job-2")

    assert second == ExecutionOutcome.FAILED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.FAILED
    assert await attempt_repo.count_by_run(run.run_id) == 2
    # 预算耗尽后不再重置 Outbox：记录保持 PENDING（上次重试遗留），
    # 派发循环看到 Run 已 FAILED 时会直接丢弃，不再投递
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.PENDING
    assert entry.attempt_count == 1


async def test_execute_error_without_outbox_entry_falls_back_to_failed(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """Outbox 记录缺失时临时错误也降级 FAILED，避免滞留 RETRY_WAIT。"""

    async def _failing_executor(_run: Run, _correlation_id: str) -> None:
        raise ValueError("解析失败")

    run = await _add_run(run_repo)  # 不准备 Outbox 记录
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _failing_executor
    )

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.FAILED


async def test_execute_cancelled_during_execution_is_skipped(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """执行期间被并发取消（执行器推进 CANCELLED）时返回 SKIPPED。"""

    async def _cancelling_executor(run: Run, correlation_id: str) -> None:
        loaded = await run_repo.get_by_id(run.run_id)
        assert loaded is not None
        await run_repo.update_status(
            run.run_id, RunStatus.RUNNING, RunStatus.CANCELLED, loaded.event_sequence + 1
        )

    run = await _add_run(run_repo)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, _cancelling_executor
    )

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.SKIPPED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.CANCELLED


async def test_concurrent_executions_only_one_completes(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    attempt_repo: FakeAttemptRepository,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """两个并发执行同一 Run 时只有一个能完成，另一个跳过。"""
    run = await _add_run(run_repo)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo,
        _completing_executor(run_repo, event_repo),
    )

    # Fake 无真实并发，顺序模拟：第一次认领成功后第二次应跳过
    first = await service.execute(run.run_id, correlation_id="job-a")
    second = await service.execute(run.run_id, correlation_id="job-b")

    assert {first, second} == {ExecutionOutcome.COMPLETED, ExecutionOutcome.SKIPPED}
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.SUCCEEDED
