"""Run Attempt Repository 的 PostgreSQL 集成测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.project import create_project
from literature_agent.domain.run import RunStatus, create_run
from literature_agent.domain.run_attempt import AttemptStatus, create_run_attempt
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


@pytest.fixture
async def running_run(session: AsyncSession) -> str:
    """创建 Project 与一个 RUNNING 状态的 Run，返回 run_id。"""
    project = create_project(owner_id="user-1", name="测试项目", description="")
    await SqlalchemyProjectRepository(session).add(project)
    await session.flush()
    run = create_run(project_id=project.project_id, owner_id="user-1", run_type="ingestion")
    await SqlalchemyRunRepository(session).add(run)
    await session.flush()
    await SqlalchemyRunRepository(session).update_status(
        run.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 2
    )
    await session.commit()
    return run.run_id


async def test_add_and_get_latest(session: AsyncSession, running_run: str) -> None:
    """保存 Attempt 后可按 Run 查询最新一条。"""
    repo = SqlalchemyAttemptRepository(session)
    first = create_run_attempt(running_run, 1, "worker-a:1")
    await repo.add(first)
    await session.flush()
    second = create_run_attempt(running_run, 2, "worker-b:2")
    await repo.add(second)
    await session.flush()

    assert await repo.count_by_run(running_run) == 2
    latest = await repo.get_latest_by_run(running_run)
    assert latest is not None
    assert latest.attempt_id == second.attempt_id
    assert latest.attempt_number == 2
    assert latest.status == AttemptStatus.RUNNING


async def test_unique_run_attempt_number(session: AsyncSession, running_run: str) -> None:
    """(run_id, attempt_number) 唯一约束生效。"""
    from sqlalchemy.exc import IntegrityError

    repo = SqlalchemyAttemptRepository(session)
    await repo.add(create_run_attempt(running_run, 1, "worker-a:1"))
    await session.flush()
    await repo.add(create_run_attempt(running_run, 1, "worker-a:1"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_heartbeat_and_finish_are_conditional(
    session: AsyncSession, running_run: str
) -> None:
    """心跳与结束只允许 RUNNING 状态，终态幂等。"""
    repo = SqlalchemyAttemptRepository(session)
    attempt = create_run_attempt(running_run, 1, "worker-a:1")
    await repo.add(attempt)
    await session.flush()

    later = datetime.now(UTC) + timedelta(seconds=30)
    assert await repo.record_heartbeat(attempt.attempt_id, later)
    await session.flush()
    latest = await repo.get_latest_by_run(running_run)
    assert latest is not None
    assert latest.heartbeat_at >= later

    # 结束后不再接受心跳或第二次结束
    assert await repo.finish_if_running(attempt.attempt_id, AttemptStatus.FAILED, later)
    await session.flush()
    assert not await repo.record_heartbeat(attempt.attempt_id, later)
    assert not await repo.finish_if_running(attempt.attempt_id, AttemptStatus.FAILED, later)

    latest = await repo.get_latest_by_run(running_run)
    assert latest is not None
    assert latest.status == AttemptStatus.FAILED
    assert latest.finished_at is not None


async def test_list_expired_running_joins_run_status(
    session: AsyncSession, running_run: str
) -> None:
    """过期查询只返回 Run 仍 RUNNING 且心跳早于 cutoff 的 Attempt。"""
    repo = SqlalchemyAttemptRepository(session)
    now = datetime.now(UTC)
    stale = now - timedelta(hours=1)

    expired = create_run_attempt(running_run, 1, "dead:1")
    await repo.add(expired)
    await session.flush()
    assert await repo.record_heartbeat(expired.attempt_id, stale)
    await session.flush()

    # Run 已到终态的过期 Attempt 不应被返回
    second_run = create_run(
        project_id=(await _project_id(session)), owner_id="user-1", run_type="ingestion"
    )
    await SqlalchemyRunRepository(session).add(second_run)
    await session.flush()
    finished_attempt = create_run_attempt(second_run.run_id, 1, "dead:2")
    await repo.add(finished_attempt)
    await session.flush()
    await repo.finish_if_running(finished_attempt.attempt_id, AttemptStatus.SUCCEEDED, stale)
    await session.flush()

    candidates = await repo.list_expired_running(now - timedelta(minutes=10), 10)
    assert [a.attempt_id for a in candidates] == [expired.attempt_id]


async def _project_id(session: AsyncSession) -> str:
    """返回测试库中第一个 Project 的 ID（测试辅助）。"""
    from sqlalchemy import select

    from literature_agent.infrastructure.persistence.models import ProjectORM

    result = await session.execute(select(ProjectORM.project_id).limit(1))
    return str(result.scalar_one())
