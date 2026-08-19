"""Run Application Service 测试。"""

import pytest
import pytest_asyncio

from literature_agent.application.run_service import RunService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import RunNotFoundError
from literature_agent.domain.run import RunStatus
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


@pytest_asyncio.fixture
async def service():
    """提供使用 Fake Repository 的 RunService。"""
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()

    def run_repo_factory(_session: object) -> FakeRunRepository:
        return run_repo

    def event_repo_factory(_session: object) -> FakeEventRepository:
        return event_repo

    yield RunService(
        session_factory=fake_session,
        run_repo_factory=run_repo_factory,
        event_repo_factory=event_repo_factory,
    )


@pytest.mark.asyncio
async def test_create_run_writes_first_event(service: RunService) -> None:
    """创建 Run 时应同时写入 run_created 事件。"""
    actor = ActorContext(owner_id="user-1")

    run = await service.create_run(
        actor,
        project_id="project-1",
        run_type="ingestion",
        input_payload={"file": "test.pdf"},
        correlation_id="corr-1",
    )

    assert run.status == RunStatus.QUEUED
    assert run.owner_id == "user-1"
    assert run.event_sequence == 2
    events = await service.list_events(actor, run.run_id)
    assert len(events) == 1
    assert events[0].sequence == 1
    assert events[0].event_type == "run_created"


@pytest.mark.asyncio
async def test_start_run(service: RunService) -> None:
    """start_run 应将状态推进到 RUNNING 并写入事件。"""
    actor = ActorContext(owner_id="user-1")
    run = await service.create_run(actor, "project-1", "ingestion", {}, "corr-1")

    updated = await service.start_run(actor, run.run_id, "corr-2")

    assert updated.status == RunStatus.RUNNING
    assert updated.event_sequence == 3
    events = await service.list_events(actor, run.run_id)
    assert len(events) == 2
    assert events[1].event_type == "run_started"
    assert events[1].sequence == 2


@pytest.mark.asyncio
async def test_complete_run(service: RunService) -> None:
    """complete_run 应将状态推进到 SUCCEEDED。"""
    actor = ActorContext(owner_id="user-1")
    run = await service.create_run(actor, "project-1", "ingestion", {}, "corr-1")
    run = await service.start_run(actor, run.run_id, "corr-2")

    updated = await service.complete_run(actor, run.run_id, {"output": "done"}, "corr-3")

    assert updated.status == RunStatus.SUCCEEDED
    assert updated.result_payload == {"output": "done"}


@pytest.mark.asyncio
async def test_cancel_queued_run(service: RunService) -> None:
    """QUEUED 的 Run 可以直接取消。"""
    actor = ActorContext(owner_id="user-1")
    run = await service.create_run(actor, "project-1", "ingestion", {}, "corr-1")

    updated = await service.cancel_run(actor, run.run_id, "corr-2")

    assert updated.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_request_cancel_running_run(service: RunService) -> None:
    """RUNNING 的 Run 先进入 CANCEL_REQUESTED。"""
    actor = ActorContext(owner_id="user-1")
    run = await service.create_run(actor, "project-1", "ingestion", {}, "corr-1")
    run = await service.start_run(actor, run.run_id, "corr-2")

    updated = await service.cancel_run(actor, run.run_id, "corr-3")

    assert updated.status == RunStatus.CANCEL_REQUESTED


@pytest.mark.asyncio
async def test_confirm_cancel_after_request(service: RunService) -> None:
    """CANCEL_REQUESTED 再次取消则进入 CANCELLED。"""
    actor = ActorContext(owner_id="user-1")
    run = await service.create_run(actor, "project-1", "ingestion", {}, "corr-1")
    run = await service.start_run(actor, run.run_id, "corr-2")
    run = await service.cancel_run(actor, run.run_id, "corr-3")

    updated = await service.cancel_run(actor, run.run_id, "corr-4")

    assert updated.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_get_run_not_found(service: RunService) -> None:
    """查询不存在的 Run 应抛 RunNotFoundError。"""
    actor = ActorContext(owner_id="user-1")

    with pytest.raises(RunNotFoundError):
        await service.get_run(actor, "00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_get_run_owned_by_other(service: RunService) -> None:
    """不能查看其他用户的 Run。"""
    actor_a = ActorContext(owner_id="user-a")
    actor_b = ActorContext(owner_id="user-b")
    run = await service.create_run(actor_a, "project-1", "ingestion", {}, "corr-1")

    with pytest.raises(RunNotFoundError):
        await service.get_run(actor_b, run.run_id)
