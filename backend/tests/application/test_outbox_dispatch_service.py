"""Outbox 派发应用服务测试。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.application.outbox_dispatch_service import OutboxDispatchService
from literature_agent.domain.queue_outbox import OutboxStatus, QueueOutbox, create_outbox_entry
from literature_agent.domain.run import Run, RunStatus, create_run
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_queue import FakeRunQueue
from tests.fakes.fake_run_repository import FakeRunRepository

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


def _make_service_with_runs(
    outbox_repo: FakeOutboxRepository,
    queue: FakeRunQueue,
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
) -> OutboxDispatchService:
    """构建注入 Run/Event Repository 的 OutboxDispatchService（切片 8）。"""
    return OutboxDispatchService(
        session_factory=fake_session,
        outbox_repo_factory=lambda _session: outbox_repo,
        queue=queue,
        max_attempts=3,
        batch_size=10,
        run_repo_factory=lambda _session: run_repo,
        event_repo_factory=lambda _session: event_repo,
    )


def _run_at(run_id: str, status: RunStatus) -> Run:
    """构造指定状态的 Run（run_created 事件占 sequence 1）。"""
    return replace(create_run(project_id="p-1", owner_id="u-1", run_type="ingestion"),
                   run_id=run_id, status=status, event_sequence=2)


async def test_retry_wait_run_is_requeued_before_dispatch(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """RETRY_WAIT 的 Run 在投递前条件转回 QUEUED 并记录 run_requeued 事件。"""
    queue = FakeRunQueue()
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    await run_repo.add(_run_at("run-1", RunStatus.RETRY_WAIT))
    await outbox_repo.add(_entry("run-1"))
    service = _make_service_with_runs(outbox_repo, queue, run_repo, event_repo)

    dispatched = await service.dispatch_pending(_NOW)

    assert dispatched == 1
    assert queue.enqueued_run_ids == ["run-1"]
    loaded = await run_repo.get_by_id("run-1")
    assert loaded is not None
    assert loaded.status == RunStatus.QUEUED
    assert [e.event_type for e in event_repo._events] == ["run_requeued"]


async def test_terminal_run_dispatch_is_dropped(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """已终态的 Run 不再投递，Outbox 记录直接标记为已投递。"""
    queue = FakeRunQueue()
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    await run_repo.add(_run_at("run-1", RunStatus.SUCCEEDED))
    await outbox_repo.add(_entry("run-1"))
    service = _make_service_with_runs(outbox_repo, queue, run_repo, event_repo)

    dispatched = await service.dispatch_pending(_NOW)

    assert dispatched == 0
    assert queue.enqueued_run_ids == []
    entry = await outbox_repo.get_by_run_id("run-1")
    assert entry is not None
    assert entry.status == OutboxStatus.DISPATCHED
    assert event_repo._events == []


async def test_cancel_requested_run_dispatch_is_dropped(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """取消中的 Run 不再投递（RETRY_WAIT 与取消竞争的收口）。"""
    queue = FakeRunQueue()
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    await run_repo.add(_run_at("run-1", RunStatus.CANCEL_REQUESTED))
    await outbox_repo.add(_entry("run-1"))
    service = _make_service_with_runs(outbox_repo, queue, run_repo, event_repo)

    dispatched = await service.dispatch_pending(_NOW)

    assert dispatched == 0
    assert queue.enqueued_run_ids == []


async def test_missing_run_dispatch_is_dropped(
    outbox_repo: FakeOutboxRepository,
) -> None:
    """Run 不存在时（脏数据）直接丢弃投递，不阻塞队列。"""
    queue = FakeRunQueue()
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    await outbox_repo.add(_entry("ghost-run"))
    service = _make_service_with_runs(outbox_repo, queue, run_repo, event_repo)

    dispatched = await service.dispatch_pending(_NOW)

    assert dispatched == 0
    entry = await outbox_repo.get_by_run_id("ghost-run")
    assert entry is not None
    assert entry.status == OutboxStatus.DISPATCHED
