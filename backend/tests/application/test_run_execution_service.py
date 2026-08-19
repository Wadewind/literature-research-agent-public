"""RunExecutionService 应用服务测试。"""

import pytest

from literature_agent.application.run_execution_service import (
    ExecutionOutcome,
    RunExecutionService,
)
from literature_agent.domain.run import Run, RunStatus, create_run
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


@pytest.fixture
def run_repo() -> FakeRunRepository:
    """提供 Fake Run Repository。"""
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    """提供 Fake Event Repository。"""
    return FakeEventRepository()


async def _placeholder_work(_run: Run) -> dict:
    """占位执行体：直接返回固定结果。"""
    return {"executor": "placeholder"}


def _make_service(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    work=_placeholder_work,
) -> RunExecutionService:
    """构建使用 Fake 依赖的 RunExecutionService。"""
    return RunExecutionService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: run_repo,
        event_repo_factory=lambda _session: event_repo,
        work=work,
    )


async def _add_run(
    run_repo: FakeRunRepository,
    status: RunStatus = RunStatus.QUEUED,
) -> Run:
    """创建一个指定状态的 Run 并存入 Fake Repository。"""
    run = create_run(project_id="p-1", owner_id="user-1", run_type="ingestion")
    if status != RunStatus.QUEUED:
        run = Run(
            run_id=run.run_id,
            project_id=run.project_id,
            owner_id=run.owner_id,
            run_type=run.run_type,
            status=status,
            input_payload=run.input_payload,
            result_payload=run.result_payload,
            event_sequence=run.event_sequence,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
    await run_repo.add(run)
    return run


def _event_types(event_repo: FakeEventRepository, run_id: str) -> list[str]:
    """返回指定 Run 的事件类型列表。"""
    return [e.event_type for e in event_repo._events if e.run_id == run_id]


async def test_execute_queued_run_completes(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
) -> None:
    """QUEUED 的 Run 应被推进到 SUCCEEDED 并写入开始/完成事件。"""
    run = await _add_run(run_repo)
    service = _make_service(run_repo, event_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.SUCCEEDED
    assert _event_types(event_repo, run.run_id) == ["run_started", "run_completed"]


async def test_execute_duplicate_job_is_skipped(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
) -> None:
    """重复 Job 不应重复推进状态或写入事件。"""
    run = await _add_run(run_repo)
    service = _make_service(run_repo, event_repo)

    first = await service.execute(run.run_id, correlation_id="job-1")
    second = await service.execute(run.run_id, correlation_id="job-1")

    assert first == ExecutionOutcome.COMPLETED
    assert second == ExecutionOutcome.SKIPPED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.SUCCEEDED
    assert _event_types(event_repo, run.run_id) == ["run_started", "run_completed"]


async def test_execute_missing_run(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
) -> None:
    """Run 不存在时返回 MISSING，不写事件。"""
    service = _make_service(run_repo, event_repo)

    outcome = await service.execute("no-such-run", correlation_id="job-1")

    assert outcome == ExecutionOutcome.MISSING
    assert event_repo._events == []


async def test_execute_cancelled_run_is_skipped(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
) -> None:
    """已取消的 Run 不应被执行。"""
    run = await _add_run(run_repo, status=RunStatus.CANCELLED)
    service = _make_service(run_repo, event_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.SKIPPED
    assert _event_types(event_repo, run.run_id) == []


async def test_execute_work_failure_marks_failed(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
) -> None:
    """执行体抛错时 Run 应进入 FAILED 并写入 run_failed 事件。"""

    async def _failing_work(_run: Run) -> dict:
        raise ValueError("解析失败")

    run = await _add_run(run_repo)
    service = _make_service(run_repo, event_repo, work=_failing_work)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.FAILED
    events = _event_types(event_repo, run.run_id)
    assert events == ["run_started", "run_failed"]
    failed_event = [e for e in event_repo._events if e.event_type == "run_failed"][0]
    assert failed_event.payload["error"]["type"] == "ValueError"
    assert failed_event.payload["error"]["message"] == "解析失败"


async def test_concurrent_executions_only_one_completes(
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
) -> None:
    """两个并发执行同一 Run 时只有一个能完成，另一个跳过。"""
    run = await _add_run(run_repo)
    service = _make_service(run_repo, event_repo)

    # Fake 无真实并发，顺序模拟：第一次认领成功后第二次应跳过
    first = await service.execute(run.run_id, correlation_id="job-a")
    second = await service.execute(run.run_id, correlation_id="job-b")

    assert {first, second} == {ExecutionOutcome.COMPLETED, ExecutionOutcome.SKIPPED}
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.SUCCEEDED
