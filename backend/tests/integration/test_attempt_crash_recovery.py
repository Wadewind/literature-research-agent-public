"""Attempt best-effort 关闭崩溃间隙的 PostgreSQL 对账测试。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.run_reconcile_service import RunReconcileService
from literature_agent.domain.event import create_event
from literature_agent.domain.project import create_project
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import RunStatus, create_run
from literature_agent.domain.run_attempt import AttemptStatus, create_run_attempt
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)

_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


async def test_concurrent_reconcile_closes_old_paused_attempt_once(db_engine) -> None:
    """父 Run 已恢复并创建新 Attempt 时，并发对账只关闭旧 Attempt。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project("user-1", "测试项目", "")
        await SqlalchemyProjectRepository(session).add(project)
        run = replace(
            create_run(project.project_id, "user-1", "review"),
            event_sequence=4,
        )
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        await SqlalchemyRunRepository(session).update_status(
            run.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 4
        )
        old = replace(create_run_attempt(run.run_id, 1, "old"), started_at=_NOW)
        new = replace(
            create_run_attempt(run.run_id, 2, "new"),
            started_at=_NOW + timedelta(seconds=20),
            heartbeat_at=_NOW + timedelta(seconds=20),
        )
        await SqlalchemyAttemptRepository(session).add(old)
        await SqlalchemyAttemptRepository(session).add(new)
        await SqlalchemyEventRepository(session).add(
            replace(
                create_event(run.run_id, 2, "dependency_wait_started", "system", "test", {}),
                occurred_at=_NOW + timedelta(seconds=10),
            )
        )
        await session.commit()

    def service() -> RunReconcileService:
        return RunReconcileService(
            session_factory=factory,
            run_repo_factory=SqlalchemyRunRepository,
            event_repo_factory=SqlalchemyEventRepository,
            attempt_repo_factory=SqlalchemyAttemptRepository,
            outbox_repo_factory=SqlalchemyOutboxRepository,
            lease_seconds=600,
            max_run_attempts=3,
        )

    results = await asyncio.gather(
        service().reconcile_orphaned_attempts(_NOW + timedelta(seconds=30)),
        service().reconcile_orphaned_attempts(_NOW + timedelta(seconds=30)),
    )
    assert sum(results) == 1
    async with factory() as session:
        attempts = await SqlalchemyAttemptRepository(session).list_by_run(run.run_id)
    assert [item.status for item in attempts] == [
        AttemptStatus.PAUSED,
        AttemptStatus.RUNNING,
    ]


async def test_concurrent_reconcile_cancels_expired_requested_run_once(db_engine) -> None:
    """取消请求后的 Worker 崩溃由并发对账单效果收敛，且不重置 Outbox。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    stale = _NOW - timedelta(seconds=601)
    async with factory() as session:
        project = create_project("user-1", "取消恢复", "")
        await SqlalchemyProjectRepository(session).add(project)
        run = replace(
            create_run(project.project_id, "user-1", "review"),
            status=RunStatus.CANCEL_REQUESTED,
            event_sequence=3,
        )
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        entry = create_outbox_entry(run.run_id)
        await SqlalchemyOutboxRepository(session).add(entry)
        await SqlalchemyOutboxRepository(session).try_mark_dispatched(entry.outbox_id, stale)
        attempt = replace(
            create_run_attempt(run.run_id, 1, "dead-worker"),
            heartbeat_at=stale,
        )
        await SqlalchemyAttemptRepository(session).add(attempt)
        await session.commit()

    def service() -> RunReconcileService:
        return RunReconcileService(
            session_factory=factory,
            run_repo_factory=SqlalchemyRunRepository,
            event_repo_factory=SqlalchemyEventRepository,
            attempt_repo_factory=SqlalchemyAttemptRepository,
            outbox_repo_factory=SqlalchemyOutboxRepository,
            lease_seconds=600,
            max_run_attempts=3,
        )

    results = await asyncio.gather(
        service().reconcile_expired(_NOW),
        service().reconcile_expired(_NOW),
    )
    assert sum(results) == 1
    async with factory() as session:
        stored_run = await SqlalchemyRunRepository(session).get_by_id(run.run_id)
        stored_attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(run.run_id)
        events = await SqlalchemyEventRepository(session).list_by_run(run.run_id)
        stored_outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(run.run_id)
    assert stored_run is not None and stored_run.status == RunStatus.CANCELLED
    assert stored_attempt is not None and stored_attempt.status == AttemptStatus.CANCELLED
    assert [event.event_type for event in events] == ["run_cancelled"]
    assert stored_outbox is not None and stored_outbox.status == OutboxStatus.DISPATCHED
