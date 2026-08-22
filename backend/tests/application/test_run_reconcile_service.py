"""Run 对账恢复应用服务测试。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.application.run_reconcile_service import RunReconcileService
from literature_agent.domain.event import create_event
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import RunStatus, create_run
from literature_agent.domain.run_attempt import AttemptStatus, create_run_attempt
from tests.fakes.fake_attempt_repository import FakeAttemptRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository

_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
_LEASE_SECONDS = 600.0


@pytest.fixture
def run_repo() -> FakeRunRepository:
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    return FakeEventRepository()


@pytest.fixture
def attempt_repo() -> FakeAttemptRepository:
    return FakeAttemptRepository()


@pytest.fixture
def outbox_repo() -> FakeOutboxRepository:
    return FakeOutboxRepository()


def _make_service(
    run_repo, event_repo, attempt_repo, outbox_repo, max_run_attempts: int = 3
) -> RunReconcileService:
    """构建使用 Fake 依赖的 RunReconcileService。"""
    return RunReconcileService(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        attempt_repo_factory=lambda _s: attempt_repo,
        outbox_repo_factory=lambda _s: outbox_repo,
        lease_seconds=_LEASE_SECONDS,
        max_run_attempts=max_run_attempts,
    )


async def _seed_running_run(
    run_repo, attempt_repo, outbox_repo, *, heartbeat_at: datetime
) -> tuple[str, str]:
    """准备崩溃现场：RUNNING Run + DISPATCHED Outbox + 指定心跳的 RUNNING Attempt。"""
    run = replace(
        create_run(project_id="p-1", owner_id="u-1", run_type="ingestion"),
        status=RunStatus.RUNNING,
        event_sequence=3,
    )
    await run_repo.add(run)
    entry = create_outbox_entry(run.run_id)
    await outbox_repo.add(entry)
    await outbox_repo.try_mark_dispatched(entry.outbox_id, heartbeat_at)
    attempt = create_run_attempt(run.run_id, 1, "dead-worker:1")
    await attempt_repo.add(attempt)
    attempt_repo.force_heartbeat(attempt.attempt_id, heartbeat_at)
    return run.run_id, attempt.attempt_id


async def _seed_finished_attempt(attempt_repo, run_id: str, number: int) -> None:
    """写入一条已结束的历史 Attempt（占用重试预算）。"""
    old = create_run_attempt(run_id, number, "dead-worker:1")
    await attempt_repo.add(old)
    await attempt_repo.finish_if_running(
        old.attempt_id, AttemptStatus.FAILED, old.started_at
    )


async def test_expired_run_is_recovered_and_rescheduled(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """lease 过期的 RUNNING Run：Attempt 关闭为 crashed，Run 转 RETRY_WAIT 待重投。"""
    stale = _NOW - timedelta(seconds=_LEASE_SECONDS + 1)
    run_id, attempt_id = await _seed_running_run(
        run_repo, attempt_repo, outbox_repo, heartbeat_at=stale
    )
    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)

    recovered = await service.reconcile_expired(_NOW)

    assert recovered == 1
    attempt = attempt_repo.get(attempt_id)
    assert attempt is not None
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.error is not None
    assert attempt.error["type"] == "worker_crashed"
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    assert run.status == RunStatus.RETRY_WAIT
    entry = await outbox_repo.get_by_run_id(run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.PENDING
    assert entry.scheduled_at > _NOW
    assert [e.event_type for e in event_repo._events] == ["run_retry_scheduled"]


async def test_fresh_heartbeat_run_is_not_touched(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """心跳未过期的 Run 不被对账收回。"""
    run_id, attempt_id = await _seed_running_run(
        run_repo, attempt_repo, outbox_repo, heartbeat_at=_NOW
    )
    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)

    assert await service.reconcile_expired(_NOW) == 0
    attempt = attempt_repo.get(attempt_id)
    assert attempt is not None
    assert attempt.status == AttemptStatus.RUNNING
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING


async def test_terminal_run_is_skipped(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """Run 已到终态（复活 Worker 已提交）时不产生第二个终态。"""
    stale = _NOW - timedelta(seconds=_LEASE_SECONDS + 1)
    run_id, attempt_id = await _seed_running_run(
        run_repo, attempt_repo, outbox_repo, heartbeat_at=stale
    )
    # 复活的原 Worker 先提交了终态
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    await run_repo.update_status(run_id, RunStatus.RUNNING, RunStatus.SUCCEEDED, 4)

    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)
    assert await service.reconcile_expired(_NOW) == 0

    run = await run_repo.get_by_id(run_id)
    assert run is not None
    assert run.status == RunStatus.SUCCEEDED
    assert event_repo._events == []


async def test_budget_exhausted_run_fails(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """重试预算耗尽时收回直接 FAILED。"""
    stale = _NOW - timedelta(seconds=_LEASE_SECONDS + 1)
    run_id, attempt_id = await _seed_running_run(
        run_repo, attempt_repo, outbox_repo, heartbeat_at=stale
    )
    # 再补两条已结束的历史 Attempt，总预算 3 次耗尽（当前为第 3 次）
    attempt = attempt_repo.get(attempt_id)
    assert attempt is not None
    # 当前执行是第 3 次尝试（心跳保持过期状态）
    attempt_repo.seed(replace(attempt, attempt_number=3))
    await _seed_finished_attempt(attempt_repo, run_id, 1)
    await _seed_finished_attempt(attempt_repo, run_id, 2)
    service = _make_service(
        run_repo, event_repo, attempt_repo, outbox_repo, max_run_attempts=3
    )

    recovered = await service.reconcile_expired(_NOW)

    assert recovered == 1
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    assert run.status == RunStatus.FAILED
    assert [e.event_type for e in event_repo._events] == ["run_failed"]
    entry = await outbox_repo.get_by_run_id(run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.DISPATCHED  # 不再重投


async def test_orphaned_paused_attempt_is_closed_without_touching_new_attempt(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """等待恢复并创建新 Attempt 后，旧残留收敛为 PAUSED，新 Attempt 保持 RUNNING。"""
    run = replace(
        create_run(project_id="p-1", owner_id="u-1", run_type="review"),
        status=RunStatus.RUNNING,
        event_sequence=4,
    )
    await run_repo.add(run)
    old = replace(create_run_attempt(run.run_id, 1, "old"), started_at=_NOW)
    new = replace(
        create_run_attempt(run.run_id, 2, "new"),
        started_at=_NOW + timedelta(seconds=20),
        heartbeat_at=_NOW + timedelta(seconds=20),
    )
    await attempt_repo.add(old)
    await attempt_repo.add(new)
    attempt_repo.set_orphaned_candidates([old])
    event_repo._events.append(
        replace(
            create_event(run.run_id, 2, "dependency_wait_started", "system", "c", {}),
            occurred_at=_NOW + timedelta(seconds=10),
        )
    )
    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)

    assert await service.reconcile_orphaned_attempts(_NOW + timedelta(seconds=30)) == 1
    assert attempt_repo.get(old.attempt_id).status == AttemptStatus.PAUSED
    assert attempt_repo.get(new.attempt_id).status == AttemptStatus.RUNNING
    assert await service.reconcile_orphaned_attempts(_NOW + timedelta(seconds=31)) == 0


async def test_orphaned_retry_attempt_uses_event_fact_not_run_type(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """Review 失败重试后的旧 Attempt 必须 FAILED，不能因 run_type 猜成 PAUSED。"""
    run = replace(
        create_run(project_id="p-1", owner_id="u-1", run_type="review"),
        status=RunStatus.QUEUED,
        event_sequence=3,
    )
    await run_repo.add(run)
    old = replace(create_run_attempt(run.run_id, 1, "old"), started_at=_NOW)
    await attempt_repo.add(old)
    attempt_repo.set_orphaned_candidates([old])
    event_repo._events.append(
        replace(
            create_event(run.run_id, 2, "run_retry_scheduled", "system", "c", {}),
            occurred_at=_NOW + timedelta(seconds=10),
        )
    )
    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)

    assert await service.reconcile_orphaned_attempts(_NOW + timedelta(seconds=30)) == 1
    assert attempt_repo.get(old.attempt_id).status == AttemptStatus.FAILED


async def test_latest_cancel_requested_attempt_remains_running(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """CANCEL_REQUESTED 仍由当前 Worker 协作收尾，不能提前关闭最新 Attempt。"""
    run = replace(
        create_run(project_id="p-1", owner_id="u-1", run_type="review"),
        status=RunStatus.CANCEL_REQUESTED,
        event_sequence=3,
    )
    await run_repo.add(run)
    current = replace(create_run_attempt(run.run_id, 1, "worker"), started_at=_NOW)
    await attempt_repo.add(current)
    attempt_repo.set_orphaned_candidates([current])
    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)

    assert await service.reconcile_orphaned_attempts(_NOW + timedelta(seconds=30)) == 0
    assert attempt_repo.get(current.attempt_id).status == AttemptStatus.RUNNING


async def test_expired_cancel_requested_run_is_cancelled_without_retry(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """协作取消期间 Worker 崩溃后，过期 lease 直接收敛取消且不重置 Outbox。"""
    stale = _NOW - timedelta(seconds=_LEASE_SECONDS + 1)
    run_id, attempt_id = await _seed_running_run(
        run_repo, attempt_repo, outbox_repo, heartbeat_at=stale
    )
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    await run_repo.update_status(
        run_id, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED, run.event_sequence + 1
    )
    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)

    assert await service.reconcile_expired(_NOW) == 1

    attempt = attempt_repo.get(attempt_id)
    assert attempt is not None
    assert attempt.status == AttemptStatus.CANCELLED
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    assert run.status == RunStatus.CANCELLED
    assert [event.event_type for event in event_repo._events] == ["run_cancelled"]
    entry = await outbox_repo.get_by_run_id(run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.DISPATCHED


async def test_fresh_cancel_requested_attempt_waits_for_worker(
    run_repo, event_repo, attempt_repo, outbox_repo
) -> None:
    """最新心跳仍新鲜时，CANCEL_REQUESTED 继续等待 Worker 协作收尾。"""
    run_id, attempt_id = await _seed_running_run(
        run_repo, attempt_repo, outbox_repo, heartbeat_at=_NOW
    )
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    await run_repo.update_status(
        run_id, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED, run.event_sequence + 1
    )
    service = _make_service(run_repo, event_repo, attempt_repo, outbox_repo)

    assert await service.reconcile_expired(_NOW) == 0
    assert attempt_repo.get(attempt_id).status == AttemptStatus.RUNNING
    run = await run_repo.get_by_id(run_id)
    assert run is not None
    assert run.status == RunStatus.CANCEL_REQUESTED
    assert event_repo._events == []
