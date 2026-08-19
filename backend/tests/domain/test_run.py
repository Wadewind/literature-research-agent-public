"""Run 领域模型与状态机测试。"""

import pytest

from literature_agent.domain.exceptions import InvalidRunTransitionError
from literature_agent.domain.run import RunStatus, create_run


def test_create_run_initial_state() -> None:
    """新创建的 Run 状态应为 QUEUED，event_sequence 为 1。"""
    run = create_run(
        project_id="project-1",
        owner_id="user-1",
        run_type="ingestion",
        input_payload={"key": "value"},
    )

    assert run.status == RunStatus.QUEUED
    assert run.event_sequence == 1
    assert run.run_type == "ingestion"
    assert run.input_payload == {"key": "value"}


def test_queued_to_running() -> None:
    """QUEUED 可以转换到 RUNNING。"""
    run = create_run(project_id="p1", owner_id="u1", run_type="ingestion")

    new_run = run.transition_to(RunStatus.RUNNING)

    assert new_run.status == RunStatus.RUNNING
    assert new_run.run_id == run.run_id


def test_running_to_succeeded() -> None:
    """RUNNING 可以转换到 SUCCEEDED。"""
    run = create_run(project_id="p1", owner_id="u1", run_type="ingestion")
    run = run.transition_to(RunStatus.RUNNING)

    new_run = run.transition_to(RunStatus.SUCCEEDED)

    assert new_run.status == RunStatus.SUCCEEDED


def test_terminal_state_cannot_transition() -> None:
    """终态不能再转换。"""
    run = create_run(project_id="p1", owner_id="u1", run_type="ingestion")
    run = run.transition_to(RunStatus.RUNNING)
    run = run.transition_to(RunStatus.SUCCEEDED)

    with pytest.raises(InvalidRunTransitionError):
        run.transition_to(RunStatus.RUNNING)


def test_illegal_transition_raises() -> None:
    """非法转换应抛 InvalidRunTransitionError。"""
    run = create_run(project_id="p1", owner_id="u1", run_type="ingestion")

    with pytest.raises(InvalidRunTransitionError):
        run.transition_to(RunStatus.SUCCEEDED)


def test_cancel_queued() -> None:
    """QUEUED 可以直接取消。"""
    run = create_run(project_id="p1", owner_id="u1", run_type="ingestion")

    new_run = run.transition_to(RunStatus.CANCELLED)

    assert new_run.status == RunStatus.CANCELLED


def test_request_cancel_running() -> None:
    """RUNNING 可以先请求取消，再确认取消。"""
    run = create_run(project_id="p1", owner_id="u1", run_type="ingestion")
    run = run.transition_to(RunStatus.RUNNING)

    run = run.transition_to(RunStatus.CANCEL_REQUESTED)
    assert run.status == RunStatus.CANCEL_REQUESTED

    run = run.transition_to(RunStatus.CANCELLED)
    assert run.status == RunStatus.CANCELLED
