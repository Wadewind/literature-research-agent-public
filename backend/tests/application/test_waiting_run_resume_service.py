"""等待 Run 正常恢复服务测试。"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from literature_agent.application.waiting_run_resume_service import (
    ResumeReason,
    WaitingRunResumeService,
)
from literature_agent.domain.exceptions import (
    RunConcurrentModificationError,
    RunNotFoundError,
    RunSchedulingError,
)
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import RunStatus, create_run
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


def _make_service(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    outbox_repo: FakeOutboxRepository,
) -> WaitingRunResumeService:
    """构造使用共享 Fake Repository 的服务。"""
    return WaitingRunResumeService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: run_repo,
        event_repo_factory=lambda _session: event_repo,
        outbox_repo_factory=lambda _session: outbox_repo,
    )


async def _seed_waiting_run(
    run_repo: FakeRunRepository,
    outbox_repo: FakeOutboxRepository,
    status: RunStatus,
) -> str:
    """准备等待 Run 及已投递 Outbox。"""
    run = create_run("project-1", "user-1", "ingestion")
    run = replace(run, status=status, event_sequence=3)
    await run_repo.add(run)
    entry = create_outbox_entry(run.run_id)
    await outbox_repo.add(entry)
    assert await outbox_repo.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))
    return run.run_id


@pytest.mark.parametrize(
    ("waiting_status", "reason", "event_type", "actor_type"),
    [
        (
            RunStatus.WAITING_DEPENDENCY,
            ResumeReason.DEPENDENCY_COMPLETED,
            "dependency_wait_completed",
            "system",
        ),
        (
            RunStatus.WAITING_INPUT,
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "human_input_submitted",
            "user",
        ),
    ],
)
async def test_resume_waiting_run_commits_run_event_and_outbox(
    waiting_status: RunStatus,
    reason: ResumeReason,
    event_type: str,
    actor_type: str,
) -> None:
    """正常恢复应一次写入 QUEUED、原因 Event 和待投递 Outbox。"""
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    run_id = await _seed_waiting_run(run_repo, outbox_repo, waiting_status)
    service = _make_service(run_repo, event_repo, outbox_repo)

    resumed = await service.resume(
        run_id=run_id,
        owner_id="user-1",
        project_id="project-1",
        reason=reason,
        correlation_id="resume-1",
        payload={"request_id": "request-1"},
    )

    assert resumed.status == RunStatus.QUEUED
    assert resumed.event_sequence == 4
    events = await event_repo.list_by_run(run_id)
    assert [(event.sequence, event.event_type) for event in events] == [(3, event_type)]
    assert events[0].actor_type == actor_type
    assert events[0].payload == {"request_id": "request-1"}
    outbox = await outbox_repo.get_by_run_id(run_id)
    assert outbox is not None
    assert outbox.status == OutboxStatus.PENDING
    assert outbox.attempt_count == 0
    assert outbox.dispatched_at is None


async def test_resume_rejects_reason_for_other_waiting_state() -> None:
    """恢复原因必须与当前等待状态匹配。"""
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    run_id = await _seed_waiting_run(
        run_repo, outbox_repo, RunStatus.WAITING_DEPENDENCY
    )
    service = _make_service(run_repo, event_repo, outbox_repo)

    with pytest.raises(RunConcurrentModificationError):
        await service.resume(
            run_id,
            "user-1",
            "project-1",
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-1",
        )

    loaded = await run_repo.get_by_id(run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.WAITING_DEPENDENCY
    assert await event_repo.list_by_run(run_id) == []


async def test_repeated_resume_has_no_second_effect() -> None:
    """重复恢复由 Run 条件状态拒绝，不重复写 Event 或重置 Outbox。"""
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    run_id = await _seed_waiting_run(run_repo, outbox_repo, RunStatus.WAITING_INPUT)
    service = _make_service(run_repo, event_repo, outbox_repo)

    await service.resume(
        run_id,
        "user-1",
        "project-1",
        ResumeReason.HUMAN_INPUT_SUBMITTED,
        "resume-1",
    )
    with pytest.raises(RunConcurrentModificationError):
        await service.resume(
            run_id,
            "user-1",
            "project-1",
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-2",
        )

    assert len(await event_repo.list_by_run(run_id)) == 1


async def test_resume_hides_other_owner_run() -> None:
    """正常恢复必须同时校验 Run 所有者。"""
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    run_id = await _seed_waiting_run(run_repo, outbox_repo, RunStatus.WAITING_INPUT)
    service = _make_service(run_repo, event_repo, outbox_repo)

    with pytest.raises(RunNotFoundError):
        await service.resume(
            run_id,
            "other-user",
            "project-1",
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-1",
        )


async def test_resume_hides_other_project_for_same_owner() -> None:
    """同一 owner 也不能跨 Project 恢复 Run。"""
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    run_id = await _seed_waiting_run(run_repo, outbox_repo, RunStatus.WAITING_INPUT)
    service = _make_service(run_repo, event_repo, outbox_repo)

    with pytest.raises(RunNotFoundError):
        await service.resume(
            run_id,
            "user-1",
            "other-project",
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-1",
        )

    loaded = await run_repo.get_by_id(run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.WAITING_INPUT
    assert await event_repo.list_by_run(run_id) == []


async def test_resume_requires_dispatched_outbox() -> None:
    """缺少可重置 Outbox 时不能声称恢复成功。"""
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    run = create_run("project-1", "user-1", "ingestion")
    await run_repo.add(replace(run, status=RunStatus.WAITING_INPUT))
    service = _make_service(run_repo, event_repo, outbox_repo)

    with pytest.raises(RunSchedulingError):
        await service.resume(
            run.run_id,
            "user-1",
            "project-1",
            ResumeReason.HUMAN_INPUT_SUBMITTED,
            "resume-1",
        )
