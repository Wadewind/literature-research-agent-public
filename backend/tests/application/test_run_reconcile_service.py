"""Run 对账恢复应用服务测试。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.application.run_reconcile_service import RunReconcileService
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
