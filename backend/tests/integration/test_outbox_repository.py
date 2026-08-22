"""Queue Outbox Repository 的 PostgreSQL 集成测试。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import create_run
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


@pytest.fixture
async def run_id(session, project: str) -> str:
    """在数据库中创建一个 Run 并返回其 ID。"""
    run = create_run(project_id=project, owner_id="user-1", run_type="ingestion")
    await SqlalchemyRunRepository(session).add(run)
    await session.commit()
    return run.run_id


async def test_add_and_get_by_run_id(session, run_id: str) -> None:
    """写入后应能按 run_id 读回一致的记录。"""
    repo = SqlalchemyOutboxRepository(session)
    entry = create_outbox_entry(run_id)

    await repo.add(entry)
    await session.commit()

    loaded = await repo.get_by_run_id(run_id)
    assert loaded is not None
    assert loaded.outbox_id == entry.outbox_id
    assert loaded.status == OutboxStatus.PENDING
    assert loaded.attempt_count == 0


async def test_run_id_unique_constraint(session, run_id: str) -> None:
    """同一 run_id 不允许第二条 Outbox 记录。"""
    repo = SqlalchemyOutboxRepository(session)
    await repo.add(create_outbox_entry(run_id))
    await session.commit()

    await repo.add(create_outbox_entry(run_id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_outbox_requires_existing_run(session) -> None:
    """Outbox 记录必须引用存在的 Run。"""
    repo = SqlalchemyOutboxRepository(session)
    await repo.add(create_outbox_entry("00000000-0000-0000-0000-000000000000"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_list_due_pending_filters_and_orders(session, project: str) -> None:
    """只返回到期的 PENDING 记录，并按 scheduled_at 升序。"""
    run_repo = SqlalchemyRunRepository(session)
    repo = SqlalchemyOutboxRepository(session)
    now = datetime.now(UTC)

    async def _add_entry(scheduled_at: datetime, status: OutboxStatus) -> None:
        run = create_run(project_id=project, owner_id="user-1", run_type="ingestion")
        await run_repo.add(run)
        # UOW 不感知表级 FK 的插入顺序，先落 Run 再写 Outbox
        await session.flush()
        entry = replace(
            create_outbox_entry(run.run_id),
            scheduled_at=scheduled_at,
            status=status,
        )
        await repo.add(entry)

    await _add_entry(now - timedelta(seconds=2), OutboxStatus.PENDING)
    await _add_entry(now - timedelta(seconds=1), OutboxStatus.PENDING)
    await _add_entry(now + timedelta(hours=1), OutboxStatus.PENDING)
    await _add_entry(now - timedelta(seconds=3), OutboxStatus.DISPATCHED)
    await session.commit()

    due = await repo.list_due_pending(now, limit=10)

    assert len(due) == 2
    assert due[0].scheduled_at <= due[1].scheduled_at
    assert all(entry.status == OutboxStatus.PENDING for entry in due)


async def test_list_due_pending_respects_limit(session, run_id: str) -> None:
    """limit 应限制返回数量。"""
    repo = SqlalchemyOutboxRepository(session)
    await repo.add(create_outbox_entry(run_id))
    await session.commit()

    assert await repo.list_due_pending(datetime.now(UTC), limit=0) == []


async def test_try_mark_dispatched_only_from_pending(session, run_id: str) -> None:
    """只有 PENDING 记录可以被标记为已投递，重复标记返回 False。"""
    repo = SqlalchemyOutboxRepository(session)
    entry = create_outbox_entry(run_id)
    await repo.add(entry)
    await session.commit()

    now = datetime.now(UTC)
    assert await repo.try_mark_dispatched(entry.outbox_id, now) is True
    await session.commit()
    # 重复标记不应生效
    assert await repo.try_mark_dispatched(entry.outbox_id, now) is False
    await session.rollback()

    loaded = await repo.get_by_run_id(run_id)
    assert loaded is not None
    assert loaded.status == OutboxStatus.DISPATCHED
    assert loaded.dispatched_at is not None


async def test_save_records_dispatch_failure(session, run_id: str) -> None:
    """save 应持久化失败次数与退避后的预定时间。"""
    repo = SqlalchemyOutboxRepository(session)
    entry = create_outbox_entry(run_id)
    await repo.add(entry)
    await session.commit()

    now = datetime.now(UTC)
    failed = entry.record_dispatch_failure(now, max_attempts=2)
    await repo.save(failed)
    await session.commit()

    loaded = await repo.get_by_run_id(run_id)
    assert loaded is not None
    assert loaded.status == OutboxStatus.PENDING
    assert loaded.attempt_count == 1
    assert loaded.scheduled_at > now

    # 再次失败达到上限后进入 FAILED
    failed_again = loaded.record_dispatch_failure(now, max_attempts=2)
    await repo.save(failed_again)
    await session.commit()

    loaded = await repo.get_by_run_id(run_id)
    assert loaded is not None
    assert loaded.status == OutboxStatus.FAILED
    assert loaded.attempt_count == 2


async def test_schedule_again_does_not_increment_attempt_count(session, run_id: str) -> None:
    """正常恢复只重置投递状态，不计作失败重试。"""
    repo = SqlalchemyOutboxRepository(session)
    entry = create_outbox_entry(run_id)
    await repo.add(entry)
    await session.commit()
    assert await repo.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))
    await session.commit()

    assert await repo.schedule_again(run_id) is True
    await session.commit()

    loaded = await repo.get_by_run_id(run_id)
    assert loaded is not None
    assert loaded.status == OutboxStatus.PENDING
    assert loaded.attempt_count == 0
    assert loaded.dispatched_at is None
    assert await repo.schedule_again(run_id) is False


async def test_reset_for_retry_increments_attempt_count(session, run_id: str) -> None:
    """失败重试与正常恢复不同，会增加失败计数。"""
    repo = SqlalchemyOutboxRepository(session)
    entry = create_outbox_entry(run_id)
    await repo.add(entry)
    await session.commit()
    assert await repo.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))
    await session.commit()

    assert await repo.reset_for_retry(run_id, datetime.now(UTC)) is True
    await session.commit()

    loaded = await repo.get_by_run_id(run_id)
    assert loaded is not None
    assert loaded.status == OutboxStatus.PENDING
    assert loaded.attempt_count == 1
