"""Run Attempt 领域模型测试。"""

from datetime import UTC, datetime, timedelta

from literature_agent.domain.run_attempt import (
    AttemptStatus,
    create_run_attempt,
)


def test_create_run_attempt_defaults() -> None:
    """新建 Attempt 应为 RUNNING，心跳与开始时间一致。"""
    attempt = create_run_attempt("run-1", 1, "worker-a:1")

    assert attempt.run_id == "run-1"
    assert attempt.attempt_number == 1
    assert attempt.worker_id == "worker-a:1"
    assert attempt.status == AttemptStatus.RUNNING
    assert attempt.started_at == attempt.heartbeat_at
    assert attempt.finished_at is None
    assert attempt.error is None


def test_record_heartbeat_updates_only_heartbeat() -> None:
    """心跳只推进 heartbeat_at，其余字段不变。"""
    attempt = create_run_attempt("run-1", 1, "worker-a:1")
    later = attempt.heartbeat_at + timedelta(seconds=30)

    updated = attempt.record_heartbeat(later)

    assert updated.heartbeat_at == later
    assert updated.started_at == attempt.started_at
    assert updated.status == AttemptStatus.RUNNING
    assert updated.finished_at is None


def test_finish_sets_terminal_status_and_error() -> None:
    """结束后状态、时间与错误信息完整。"""
    attempt = create_run_attempt("run-1", 2, "worker-a:1")
    now = datetime.now(UTC)
    error = {"type": "worker_crashed", "message": "lease 过期"}

    finished = attempt.finish(AttemptStatus.FAILED, now, error)

    assert finished.status == AttemptStatus.FAILED
    assert finished.finished_at == now
    assert finished.heartbeat_at == now
    assert finished.error == error


def test_finish_as_paused_is_normal_without_error() -> None:
    """等待输入或依赖时，Attempt 以 PAUSED 正常释放 Worker。"""
    attempt = create_run_attempt("run-1", 1, "worker-a:1")
    now = datetime.now(UTC)

    finished = attempt.finish(AttemptStatus.PAUSED, now)

    assert finished.status == AttemptStatus.PAUSED
    assert finished.finished_at == now
    assert finished.error is None
