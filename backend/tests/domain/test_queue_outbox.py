"""QueueOutbox 领域实体测试。"""

from datetime import UTC, datetime, timedelta

from literature_agent.domain.queue_outbox import (
    OutboxStatus,
    compute_dispatch_backoff,
    create_outbox_entry,
)


def test_create_outbox_entry_defaults() -> None:
    """新建的 Outbox 记录应为 PENDING 且立即可派发。"""
    entry = create_outbox_entry("run-1")

    assert entry.run_id == "run-1"
    assert entry.status == OutboxStatus.PENDING
    assert entry.attempt_count == 0
    assert entry.dispatched_at is None
    assert entry.scheduled_at <= datetime.now(UTC)


def test_mark_dispatched() -> None:
    """标记投递成功后状态应为 DISPATCHED 并记录时间。"""
    entry = create_outbox_entry("run-1")
    now = datetime.now(UTC)

    dispatched = entry.mark_dispatched(now)

    assert dispatched.status == OutboxStatus.DISPATCHED
    assert dispatched.dispatched_at == now
    assert dispatched.updated_at == now
    # 原实体不可变
    assert entry.status == OutboxStatus.PENDING


def test_record_dispatch_failure_backoff() -> None:
    """投递失败应增加尝试次数并按指数退避推迟下一次派发。"""
    entry = create_outbox_entry("run-1")
    now = datetime.now(UTC)

    failed_once = entry.record_dispatch_failure(now, max_attempts=10)

    assert failed_once.status == OutboxStatus.PENDING
    assert failed_once.attempt_count == 1
    assert failed_once.scheduled_at == now + timedelta(seconds=1)

    failed_twice = failed_once.record_dispatch_failure(now, max_attempts=10)
    assert failed_twice.attempt_count == 2
    assert failed_twice.scheduled_at == now + timedelta(seconds=2)


def test_record_dispatch_failure_exhausts_to_failed() -> None:
    """达到最大尝试次数后应进入 FAILED 终态。"""
    entry = create_outbox_entry("run-1")
    now = datetime.now(UTC)

    exhausted = entry.record_dispatch_failure(now, max_attempts=1)

    assert exhausted.status == OutboxStatus.FAILED
    assert exhausted.attempt_count == 1


def test_backoff_has_upper_bound() -> None:
    """退避时长不应超过上限。"""
    assert compute_dispatch_backoff(100) == timedelta(seconds=60)
