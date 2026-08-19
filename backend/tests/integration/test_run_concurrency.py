"""Run 并发转换集成测试。"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.run_service import RunService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.project import create_project
from literature_agent.domain.run import RunStatus
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


@pytest.mark.asyncio
async def test_concurrent_start_run_only_one_succeeds(db_engine) -> None:
    """并发调用 start_run 时，仅有一个成功转换，Event sequence 无重复。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # 创建测试 Project
    async with factory() as session:
        project = create_project(owner_id="user-1", name="并发测试", description="")
        await SqlalchemyProjectRepository(session).add(project)
        await session.commit()

    actor = ActorContext(owner_id="user-1")
    service = RunService(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )

    # 创建 Run 并写入首个事件
    run = await service.create_run(
        actor,
        project.project_id,
        "ingestion",
        {},
        "corr-create",
    )
    run_id = run.run_id

    async def attempt_start():
        """尝试启动 Run。"""
        return await service.start_run(actor, run_id, "corr-start")

    # 5 个并发调用
    results = await asyncio.gather(
        *(attempt_start() for _ in range(5)),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, BaseException)]

    assert len(successes) == 1
    assert successes[0].status == RunStatus.RUNNING
    assert len(errors) == 4
    assert all(isinstance(e, RunConcurrentModificationError) for e in errors)

    # 验证最终状态与事件序列
    async with factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        event_repo = SqlalchemyEventRepository(session)
        final_run = await run_repo.get_by_id(run_id)
        assert final_run is not None
        assert final_run.status == RunStatus.RUNNING
        events = await event_repo.list_by_run(run_id)
        sequences = [e.sequence for e in events]
        assert sequences == [1, 2]
