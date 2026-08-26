from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
    RuntimeExecutionControlService,
)
from literature_agent.domain.run import RunStatus, create_run
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt
from tests.fakes.fake_attempt_repository import FakeAttemptRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository
from tests.fakes.fake_runtime_execution_repository import FakeRuntimeExecutionRepository

_NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)


async def _service():
    run = create_run(project_id="project-1", owner_id="owner-1", run_type="agent_turn")
    run = run.transition_to(RunStatus.RUNNING)
    run_repo = FakeRunRepository()
    await run_repo.add(run)
    attempt_repo = FakeAttemptRepository()
    attempt = RunAttempt(
        attempt_id="attempt-1",
        run_id=run.run_id,
        attempt_number=1,
        worker_id="worker-a",
        status=AttemptStatus.RUNNING,
        started_at=_NOW,
        heartbeat_at=_NOW,
    )
    await attempt_repo.add(attempt)
    execution_repo = FakeRuntimeExecutionRepository()
    service = RuntimeExecutionControlService(
        session_factory=fake_session,
        run_repo_factory=lambda _: run_repo,
        attempt_repo_factory=lambda _: attempt_repo,
        execution_repo_factory=lambda _: execution_repo,
        lease_seconds=30,
        clock=lambda: _NOW,
    )
    return service, run, attempt, attempt_repo, execution_repo


async def test_control_claims_once_and_only_one_recovery_owner_wins() -> None:
    service, run, attempt, attempt_repo, _ = await _service()
    first = await service.claim(
        turn_run_id=run.run_id,
        session_id="session-1",
        runtime_execution_id="execution-1",
        request_hash="a" * 64,
        owner_id="runtime-a",
    )
    assert first.current_attempt_id == attempt.attempt_id
    assert first.fencing_token == 1

    await attempt_repo.finish_if_running(
        attempt.attempt_id, AttemptStatus.FAILED, _NOW + timedelta(seconds=31)
    )
    second_attempt = RunAttempt(
        attempt_id="attempt-2",
        run_id=run.run_id,
        attempt_number=2,
        worker_id="worker-b",
        status=AttemptStatus.RUNNING,
        started_at=_NOW + timedelta(seconds=31),
        heartbeat_at=_NOW + timedelta(seconds=31),
    )
    await attempt_repo.add(second_attempt)
    service._clock = lambda: _NOW + timedelta(seconds=31)  # noqa: SLF001
    second = await service.claim(
        turn_run_id=run.run_id,
        session_id="session-1",
        runtime_execution_id="execution-1",
        request_hash="a" * 64,
        owner_id="runtime-b",
    )
    assert second.current_attempt_id == second_attempt.attempt_id
    assert second.fencing_token == 2

    with pytest.raises(RuntimeExecutionControlError, match="lease"):
        await service.assert_active(first.permit)


async def test_control_rejects_cancelled_run_before_runtime_claim() -> None:
    service, run, _, _, _ = await _service()
    await service._run_repo_factory(None).update_status(  # type: ignore[arg-type]  # noqa: SLF001
        run.run_id,
        RunStatus.RUNNING,
        RunStatus.CANCEL_REQUESTED,
        run.event_sequence + 1,
    )
    with pytest.raises(RuntimeExecutionControlError, match="取消"):
        await service.claim(
            turn_run_id=run.run_id,
            session_id="session-1",
            runtime_execution_id="execution-1",
            request_hash="a" * 64,
            owner_id="runtime-a",
        )


async def test_control_cancels_runtime_only_after_business_cancel_request() -> None:
    service, run, _, _, _ = await _service()
    execution = await service.claim(
        turn_run_id=run.run_id,
        session_id="session-1",
        runtime_execution_id="execution-1",
        request_hash="a" * 64,
        owner_id="runtime-a",
    )

    with pytest.raises(RuntimeExecutionControlError, match="取消"):
        await service.cancel_for_business(run.run_id)

    assert (await service.get(run.run_id)) == execution
    await service._run_repo_factory(None).update_status(  # type: ignore[arg-type]  # noqa: SLF001
        run.run_id,
        RunStatus.RUNNING,
        RunStatus.CANCEL_REQUESTED,
        run.event_sequence + 1,
    )
    cancelled = await service.cancel_for_business(run.run_id)
    assert cancelled is not None
    assert cancelled.state.value == "cancelled"


@pytest.mark.parametrize("operation", ["temporary", "permanent"])
async def test_expired_owner_cannot_record_runtime_error(operation: str) -> None:
    service, run, _, _, _ = await _service()
    execution = await service.claim(
        turn_run_id=run.run_id,
        session_id="session-1",
        runtime_execution_id="execution-1",
        request_hash="a" * 64,
        owner_id="runtime-a",
    )
    service._clock = lambda: _NOW + timedelta(seconds=31)  # noqa: SLF001

    with pytest.raises(RuntimeExecutionControlError, match="lease"):
        if operation == "temporary":
            await service.temporary_error(
                execution.permit, code="temporary", safe_message="暂时失败"
            )
        else:
            await service.fail(
                execution.permit, code="permanent", safe_message="永久失败"
            )

    stored = await service.get(run.run_id)
    assert stored == execution


async def test_checkpoint_write_rechecks_attempt_inside_mutation_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活动性预检后 Attempt 失效时，控制记录写事务必须再次拒绝旧 owner。"""
    service, run, attempt, attempt_repo, _ = await _service()
    execution = await service.claim(
        turn_run_id=run.run_id,
        session_id="session-1",
        runtime_execution_id="execution-1",
        request_hash="a" * 64,
        owner_id="runtime-a",
    )
    original_assert_active = service.assert_active

    async def lose_attempt_after_precheck(permit):
        active = await original_assert_active(permit)
        await attempt_repo.finish_if_running(
            attempt.attempt_id, AttemptStatus.FAILED, _NOW
        )
        return active

    monkeypatch.setattr(service, "assert_active", lose_attempt_after_precheck)

    with pytest.raises(RuntimeExecutionControlError, match="lease"):
        await service.record_checkpoint(execution.permit, "checkpoint-1")

    assert await service.get(run.run_id) == execution
