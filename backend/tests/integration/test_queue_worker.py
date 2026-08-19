"""Queue/Worker 端到端集成测试：ARQ + Valkey + PostgreSQL。

验证切片 5 的最小闭环：
- Outbox 派发器把 run_id 投递到真实 ARQ/Valkey；
- 真实 ARQ Worker（burst 模式）执行 Job 并推进 Run 状态；
- 队列不可用时记录失败并退避，恢复后可补投；
- 相同 Job ID 的重复投递被队列去重。
"""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from arq.connections import RedisSettings, create_pool
from arq.worker import Worker
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.redis import RedisContainer

from literature_agent.application.outbox_dispatch_service import OutboxDispatchService
from literature_agent.application.run_execution_service import RunExecutionService
from literature_agent.domain.project import create_project
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import RunStatus, create_run
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
from literature_agent.infrastructure.queue.arq_run_queue import ArqRunQueue
from literature_agent.worker import execute_run, placeholder_work


@pytest_asyncio.fixture
async def valkey_url():
    """启动 Testcontainers Valkey 并返回连接串。"""
    with RedisContainer("valkey/valkey:9") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def queued_run(db_engine) -> str:
    """在数据库中创建 Project、QUEUED Run 和对应 Outbox 记录，返回 run_id。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project(owner_id="user-1", name="测试项目", description="")
        await SqlalchemyProjectRepository(session).add(project)
        run = create_run(
            project_id=project.project_id,
            owner_id="user-1",
            run_type="ingestion",
        )
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        await SqlalchemyOutboxRepository(session).add(create_outbox_entry(run.run_id))
        await session.commit()
        return run.run_id


def _session_factory(db_engine):
    """返回基于测试引擎的会话工厂。"""
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def test_dispatch_to_worker_completes_run(
    db_engine, valkey_url: str, queued_run: str
) -> None:
    """完整闭环：Outbox → ARQ → Worker → Run SUCCEEDED。"""
    queue = ArqRunQueue(valkey_url)
    session_factory = _session_factory(db_engine)
    dispatch_service = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=queue,
        max_attempts=10,
        batch_size=20,
    )

    dispatched = await dispatch_service.dispatch_pending()
    assert dispatched == 1

    # 重复派发同一 run_id：Outbox 已标记，不会重复投递
    assert await dispatch_service.dispatch_pending() == 0

    execution_service = RunExecutionService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        work=placeholder_work,
    )
    worker = Worker(
        redis_settings=RedisSettings.from_dsn(valkey_url),
        functions=[execute_run],
        burst=True,
        handle_signals=False,
        max_tries=1,
        ctx={"run_execution_service": execution_service},
    )
    await worker.async_run()

    async with session_factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(queued_run)
        assert run is not None
        assert run.status == RunStatus.SUCCEEDED
        events = await SqlalchemyEventRepository(session).list_by_run(queued_run)
        assert [e.event_type for e in events] == ["run_started", "run_completed"]

    await queue.aclose()


async def test_dispatch_failure_then_recovers(db_engine, valkey_url: str, queued_run: str) -> None:
    """队列不可用时记录失败并退避；队列恢复后补投成功。"""
    # 指向不可达地址，模拟 Valkey 故障
    broken_queue = ArqRunQueue("redis://127.0.0.1:6399/0")
    session_factory = _session_factory(db_engine)
    dispatch_service = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=broken_queue,
        max_attempts=10,
        batch_size=20,
    )

    assert await dispatch_service.dispatch_pending() == 0
    async with session_factory() as session:
        entry = await SqlalchemyOutboxRepository(session).get_by_run_id(queued_run)
        assert entry is not None
        assert entry.status == OutboxStatus.PENDING
        assert entry.attempt_count == 1

    # “队列恢复”后补投成功（退避窗口内注入 now 快进）
    recovered = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=ArqRunQueue(valkey_url),
        max_attempts=10,
        batch_size=20,
    )
    future = datetime.now(UTC) + timedelta(seconds=5)
    assert await recovered.dispatch_pending(future) == 1
    async with session_factory() as session:
        entry = await SqlalchemyOutboxRepository(session).get_by_run_id(queued_run)
        assert entry is not None
        assert entry.status == OutboxStatus.DISPATCHED


async def test_duplicate_enqueue_deduplicated_by_job_id(valkey_url: str) -> None:
    """相同 Job ID 的重复投递在 ARQ 侧去重。"""
    pool = await create_pool(RedisSettings.from_dsn(valkey_url))
    try:
        job1 = await pool.enqueue_job("execute_run", "run-x", _job_id="run:run-x")
        job2 = await pool.enqueue_job("execute_run", "run-x", _job_id="run:run-x")
        assert job1 is not None
        assert job2 is None
    finally:
        await pool.aclose()
