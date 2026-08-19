"""Run PostgreSQL Repository 集成测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.run import RunStatus, create_run
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


@pytest.fixture
def run_repo(session: AsyncSession) -> SqlalchemyRunRepository:
    """提供基于真实会话的 Run Repository。"""
    return SqlalchemyRunRepository(session)


@pytest.fixture
def event_repo(session: AsyncSession) -> SqlalchemyEventRepository:
    """提供基于真实会话的 Event Repository。"""
    return SqlalchemyEventRepository(session)


@pytest.mark.asyncio
async def test_add_and_get_run(
    run_repo: SqlalchemyRunRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """保存 Run 后应能按 ID 查询到。"""
    run = create_run(
        project_id=project,
        owner_id="user-1",
        run_type="ingestion",
        input_payload={"file": "test.pdf"},
    )

    await run_repo.add(run)
    await session.commit()

    fetched = await run_repo.get_by_id(run.run_id)
    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_get_by_id_for_update_ownsership(
    run_repo: SqlalchemyRunRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """get_by_id_for_update 同时校验所有者。"""
    run = create_run(project_id=project, owner_id="user-a", run_type="ingestion")
    await run_repo.add(run)
    await session.commit()

    fetched = await run_repo.get_by_id_for_update(run.run_id, "user-a")
    assert fetched is not None

    not_fetched = await run_repo.get_by_id_for_update(run.run_id, "user-b")
    assert not_fetched is None


@pytest.mark.asyncio
async def test_update_status_conditional(
    run_repo: SqlalchemyRunRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """update_status 仅在当前状态匹配时才更新。"""
    run = create_run(project_id=project, owner_id="user-1", run_type="ingestion")
    await run_repo.add(run)
    await session.commit()

    success = await run_repo.update_status(
        run.run_id,
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        2,
    )

    assert success is True
    fetched = await run_repo.get_by_id(run.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.RUNNING
    assert fetched.event_sequence == 2

    failed = await run_repo.update_status(
        run.run_id,
        RunStatus.QUEUED,
        RunStatus.SUCCEEDED,
        3,
    )
    assert failed is False
