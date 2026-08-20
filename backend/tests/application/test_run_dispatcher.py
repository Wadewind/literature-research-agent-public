"""RunDispatcher（Worker run_type 分发）应用服务测试。"""

from dataclasses import replace

import pytest

from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.domain.event import create_event
from literature_agent.domain.run import Run, RunStatus, RunType, create_run
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


@pytest.fixture
def run_repo() -> FakeRunRepository:
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    return FakeEventRepository()


class _StubExecutor:
    """记录调用的执行器桩。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, run: Run, correlation_id: str) -> None:
        """记录一次调用，不推进状态。"""
        self.calls.append(run.run_id)


async def _add_running_run(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    run_type: str,
) -> Run:
    """创建并认领一个 RUNNING 状态的 Run（模拟 Worker 认领后）。"""
    run = create_run(
        project_id="p-1", owner_id="user-1", run_type=run_type, input_payload={}
    )
    await run_repo.add(run)
    await event_repo.add(
        create_event(
            run_id=run.run_id,
            sequence=1,
            event_type="run_created",
            actor_type="user",
            correlation_id="test",
            payload={},
        )
    )
    await run_repo.update_status(run.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3)
    await event_repo.add(
        create_event(
            run_id=run.run_id,
            sequence=2,
            event_type="run_started",
            actor_type="system",
            correlation_id="test",
            payload={},
        )
    )
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    return loaded


def _make_dispatcher(run_repo, event_repo, executors) -> RunDispatcher:
    """构建使用 Fake 依赖的 RunDispatcher。"""
    return RunDispatcher(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        executors=executors,
    )


async def test_dispatches_to_registered_executor(run_repo, event_repo) -> None:
    """已注册类型分发到对应执行器。"""
    ingestion = _StubExecutor()
    indexing = _StubExecutor()
    dispatcher = _make_dispatcher(
        run_repo, event_repo,
        {RunType.INGESTION: ingestion.execute, RunType.INDEXING: indexing.execute},
    )
    run = await _add_running_run(run_repo, event_repo, RunType.INDEXING)

    await dispatcher.execute(run, correlation_id="job-1")

    assert indexing.calls == [run.run_id]
    assert ingestion.calls == []


async def test_unknown_run_type_fails_run(run_repo, event_repo) -> None:
    """未知 run_type：Run 推进 FAILED + run_failed 事件，不静默执行。"""
    ingestion = _StubExecutor()
    dispatcher = _make_dispatcher(
        run_repo, event_repo, {RunType.INGESTION: ingestion.execute}
    )
    # create_run 受 RunType 约束，直接构造非法类型模拟历史/脏数据
    run = await _add_running_run(run_repo, event_repo, RunType.INGESTION)
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    await run_repo.add(replace(loaded, run_type="mystery_type"))
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None

    await dispatcher.execute(loaded, correlation_id="job-1")

    assert ingestion.calls == []
    final = await run_repo.get_by_id(run.run_id)
    assert final is not None
    assert final.status == RunStatus.FAILED
    events = sorted(
        [e for e in event_repo._events if e.run_id == run.run_id],
        key=lambda e: e.sequence,
    )
    assert [e.event_type for e in events] == ["run_created", "run_started", "run_failed"]
    assert events[-1].payload["error"]["type"] == "unknown_run_type"


async def test_unregistered_known_type_fails_run(run_repo, event_repo) -> None:
    """已枚举但未接线的类型（rag_answer）同样显式失败。"""
    ingestion = _StubExecutor()
    dispatcher = _make_dispatcher(
        run_repo, event_repo, {RunType.INGESTION: ingestion.execute}
    )
    run = await _add_running_run(run_repo, event_repo, RunType.RAG_ANSWER)

    await dispatcher.execute(run, correlation_id="job-1")

    final = await run_repo.get_by_id(run.run_id)
    assert final is not None
    assert final.status == RunStatus.FAILED
    events = [e for e in event_repo._events if e.run_id == run.run_id]
    failed = [e for e in events if e.event_type == "run_failed"]
    assert failed[0].payload["error"]["type"] == "unknown_run_type"
