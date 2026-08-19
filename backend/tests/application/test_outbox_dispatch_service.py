"""Outbox 派发应用服务测试。"""

from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest

from literature_agent.application.outbox_dispatch_service import OutboxDispatchService
from literature_agent.domain.queue_outbox import OutboxStatus, QueueOutbox, create_outbox_entry
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_queue import FakeRunQueue

_NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def _entry(run_id: str) -> QueueOutbox:
    """构造一条预定时间固定为 _NOW 的待派发记录。"""
    return replace(create_outbox_entry(run_id), scheduled_at=_NOW)


@pytest.fixture
def outbox_repo() -> FakeOutboxRepository:
    """提供 Fake Outbox Repository。"""
    return FakeOutboxRepository()


def _make_service(
    outbox_repo: FakeOutboxRepository,
    queue: FakeRunQueue,
    max_attempts: int = 3,
) -> OutboxDispatchService:
    """构建使用 Fake 依赖的 OutboxDispatchService。"""
    return OutboxDispatchService(
        session_factory=fake_session,
        outbox_repo_factory=lambda _session: outbox_repo,
        queue=queue,
        max_attempts=max_attempts,
        batch_size=10,
    )


async def test_dispatch_pending_enqueues_and_marks_dispatched(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """到期记录应被投递并标记为 DISPATCHED。"""
    queue = FakeRunQueue()
    entry = _entry("run-1")
    await outbox_repo.add(entry)
    service = _make_service(outbox_repo, queue)

    dispatched = await service.dispatch_pending(_NOW)

    assert dispatched == 1
    assert queue.enqueued_run_ids == ["run-1"]
    loaded = await outbox_repo.get_by_run_id("run-1")
    assert loaded is not None
    assert loaded.status == OutboxStatus.DISPATCHED
    assert loaded.dispatched_at == _NOW


async def test_dispatch_skips_not_due_records(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """未到 scheduled_at 的记录不应被派发。"""
    queue = FakeRunQueue()
    entry = create_outbox_entry("run-1").record_dispatch_failure(_NOW, max_attempts=10)
    await outbox_repo.add(entry)
    service = _make_service(outbox_repo, queue)

    dispatched = await service.dispatch_pending(_NOW)

    assert dispatched == 0
    assert queue.enqueued_run_ids == []


async def test_dispatch_failure_records_attempt_and_backoff(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """投递失败应保持 PENDING、累计次数并推迟下一次尝试。"""
    queue = FakeRunQueue(fail=True)
    entry = _entry("run-1")
    await outbox_repo.add(entry)
    service = _make_service(outbox_repo, queue)

    dispatched = await service.dispatch_pending(_NOW)

    assert dispatched == 0
    loaded = await outbox_repo.get_by_run_id("run-1")
    assert loaded is not None
    assert loaded.status == OutboxStatus.PENDING
    assert loaded.attempt_count == 1
    assert loaded.scheduled_at > _NOW


async def test_dispatch_failure_exhausts_to_failed(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """达到最大尝试次数后进入 FAILED，不再派发。"""
    queue = FakeRunQueue(fail=True)
    entry = _entry("run-1")
    await outbox_repo.add(entry)
    service = _make_service(outbox_repo, queue, max_attempts=2)

    now = _NOW
    await service.dispatch_pending(now)
    # 快进过退避窗口，触发第二次（达到上限的）失败
    now = now + timedelta(seconds=2)
    await service.dispatch_pending(now)

    loaded = await outbox_repo.get_by_run_id("run-1")
    assert loaded is not None
    assert loaded.status == OutboxStatus.FAILED
    assert loaded.attempt_count == 2

    # FAILED 终态不再参与派发
    now = now + timedelta(hours=1)
    assert await service.dispatch_pending(now) == 0


async def test_redispatch_after_crash_is_safe(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """投递后标记前崩溃的场景：记录保持 PENDING，可安全重复投递。"""
    queue = FakeRunQueue()
    entry = _entry("run-1")
    await outbox_repo.add(entry)

    # 模拟“投递成功但进程在标记前崩溃”：队列已收到 Job，记录仍 PENDING
    await queue.enqueue_run("run-1")

    service = _make_service(outbox_repo, queue)
    dispatched = await service.dispatch_pending(_NOW)

    # 重复投递同一 run_id 是安全的（队列端按 Job ID 去重）
    assert dispatched == 1
    assert queue.enqueued_run_ids == ["run-1", "run-1"]
    loaded = await outbox_repo.get_by_run_id("run-1")
    assert loaded is not None
    assert loaded.status == OutboxStatus.DISPATCHED
