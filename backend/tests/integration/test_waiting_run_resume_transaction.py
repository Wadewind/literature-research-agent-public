"""等待 Run 恢复事务的 PostgreSQL 集成测试。"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from literature_agent.application.waiting_run_resume_service import (
    ResumeReason,
    WaitingRunResumeService,
)
from literature_agent.domain.exceptions import RunSchedulingError
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import RunStatus, create_run
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.models import RunORM
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


async def _seed_waiting_run(
    factory: async_sessionmaker[AsyncSession],
    project_id: str,
    *,
    outbox_dispatched: bool,
) -> str:
    """持久化一个 WAITING_INPUT Run 及指定状态的 Outbox。"""
    async with factory() as session:
        run = replace(
            create_run(project_id, "user-1", "ingestion"),
            status=RunStatus.WAITING_INPUT,
            event_sequence=2,
        )
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        entry = create_outbox_entry(run.run_id)
        outbox_repo = SqlalchemyOutboxRepository(session)
        await outbox_repo.add(entry)
        await session.flush()
        if outbox_dispatched:
            assert await outbox_repo.try_mark_dispatched(
                entry.outbox_id, datetime.now(UTC)
            )
        await session.commit()
        return run.run_id


def _service(
    factory: async_sessionmaker[AsyncSession],
) -> WaitingRunResumeService[AsyncSession]:
    """构造真实 PostgreSQL Repository 服务。"""
    return WaitingRunResumeService(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
    )


async def test_resume_commits_run_event_and_outbox_together(db_engine, project: str) -> None:
    """外层可在同一 session 组合前置记录与恢复三项效果后统一提交。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run_id = await _seed_waiting_run(factory, project, outbox_dispatched=True)

    async with factory() as session:
        # 以 result_payload marker 模拟切片 2 才会引入的 HumanInput/Dependency 记录；
        # 关键是该写入与 resume_in_session 共享 session，由外层唯一 commit。
        await session.execute(
            update(RunORM)
            .where(RunORM.run_id == run_id)
            .values(result_payload={"reason_record_marker": "input-1"})
        )
        await _service(factory).resume_in_session(
            session,
            run_id,
            "user-1",
            project,
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-1",
        )

        # resume_in_session 不自行提交；独立事务在外层 commit 前仍只能看到旧状态。
        async with factory() as observer:
            uncommitted = await SqlalchemyRunRepository(observer).get_by_id(run_id)
            assert uncommitted is not None
            assert uncommitted.status == RunStatus.WAITING_INPUT
            assert uncommitted.result_payload == {}
        await session.commit()

    async with factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(run_id)
        events = await SqlalchemyEventRepository(session).list_by_run(run_id)
    assert run is not None and run.status == RunStatus.QUEUED
    assert run.event_sequence == 3
    assert run.result_payload == {"reason_record_marker": "input-1"}
    assert outbox is not None and outbox.status == OutboxStatus.PENDING
    assert outbox.attempt_count == 0
    assert [event.event_type for event in events] == ["human_input_submitted"]


async def test_outbox_failure_or_exception_rolls_back_run_and_event(
    db_engine, project: str
) -> None:
    """Outbox 条件失败或抛错时均回滚 Run/Event，不留下部分业务效果。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run_id = await _seed_waiting_run(factory, project, outbox_dispatched=False)

    with pytest.raises(RunSchedulingError):
        await _service(factory).resume(
            run_id,
            "user-1",
            project,
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-1",
        )

    async with factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(run_id)
        events = await SqlalchemyEventRepository(session).list_by_run(run_id)
    assert run is not None and run.status == RunStatus.WAITING_INPUT
    assert run.event_sequence == 2
    assert outbox is not None and outbox.status == OutboxStatus.PENDING
    assert events == []

    class _RaisingOutboxRepository(SqlalchemyOutboxRepository):
        """模拟 Outbox Adapter 在条件更新时异常。"""

        async def schedule_again(self, run_id: str) -> bool:
            raise RuntimeError(f"Outbox 写入失败: {run_id}")

    exception_run_id = await _seed_waiting_run(
        factory, project, outbox_dispatched=True
    )
    service = WaitingRunResumeService(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=_RaisingOutboxRepository,
    )

    with pytest.raises(RuntimeError, match="Outbox 写入失败"):
        await service.resume(
            exception_run_id,
            "user-1",
            project,
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-2",
        )

    async with factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(exception_run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(exception_run_id)
        events = await SqlalchemyEventRepository(session).list_by_run(exception_run_id)
    assert run is not None and run.status == RunStatus.WAITING_INPUT
    assert run.event_sequence == 2
    assert outbox is not None and outbox.status == OutboxStatus.DISPATCHED
    assert events == []
