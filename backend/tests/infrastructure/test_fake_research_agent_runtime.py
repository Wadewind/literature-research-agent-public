"""完全离线 FakeResearchAgentRuntime 契约测试。"""

import pytest

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEventKind,
    RuntimeExecutionState,
    RuntimeResumeRequest,
    RuntimeTurnRequest,
)
from literature_agent.domain.research_agent import (
    create_context_snapshot,
    create_policy_snapshot,
)
from literature_agent.infrastructure.agent.fake_research_agent_runtime import (
    FakeResearchAgentRuntime,
)


def _request(*, session_id: str = "session-1", turn_run_id: str = "turn-1") -> RuntimeTurnRequest:
    context = create_context_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id=session_id,
        turn_run_id=turn_run_id,
        user_message_id="message-1",
        history_through_sequence=0,
        review_output_id="review-output-1",
    )
    policy = create_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id=session_id,
        turn_run_id=turn_run_id,
        max_model_calls=2,
        max_tool_calls=0,
    )
    return RuntimeTurnRequest(
        session_id=session_id,
        turn_run_id=turn_run_id,
        user_message_id="message-1",
        user_message_content="比较证据中的两类方法",
        context_snapshot=context,
        policy_snapshot=policy,
    )


async def _collect(stream: object) -> list[object]:
    return [event async for event in stream]  # type: ignore[union-attr]


async def test_fake_runtime_is_deterministic_and_keeps_stable_bindings() -> None:
    """同一 Session/Turn 始终映射相同 Thread/Workspace/Execution/Checkpoint。"""
    runtime = FakeResearchAgentRuntime()
    request = _request()

    first_events = await _collect(runtime.execute_turn(request))
    first_status = await runtime.reconcile_turn("turn-1")
    first_result = await runtime.collect_turn_result("turn-1")
    repeated_events = await _collect(runtime.execute_turn(request))
    repeated_status = await runtime.reconcile_turn("turn-1")
    repeated_result = await runtime.collect_turn_result("turn-1")

    assert first_events == repeated_events
    assert first_result == repeated_result
    assert first_status == repeated_status
    assert first_status.state is RuntimeExecutionState.SUCCEEDED
    assert first_status.session_binding.binding_id.startswith("fake-binding-")
    assert first_status.session_binding.generation == 1
    assert first_status.session_binding.runtime_thread_id.startswith("fake-thread-")
    assert first_status.session_binding.runtime_workspace_id.startswith("fake-workspace-")
    assert first_status.turn_binding.runtime_execution_id.startswith("fake-execution-")
    assert first_status.turn_binding.runtime_checkpoint_id.startswith("fake-checkpoint-")
    assert (
        first_status.turn_binding.session_binding_id
        == first_status.session_binding.binding_id
    )
    assert runtime.execution_start_count == 1
    assert runtime.session_binding_count == 1
    assert runtime.turn_binding_count == 1


async def test_same_session_uses_one_thread_across_turns() -> None:
    """Session 绑定一个 Thread/Workspace，不同 Turn 各有一个 Execution。"""
    runtime = FakeResearchAgentRuntime()

    await _collect(runtime.execute_turn(_request(turn_run_id="turn-1")))
    await _collect(runtime.execute_turn(_request(turn_run_id="turn-2")))
    first = await runtime.reconcile_turn("turn-1")
    second = await runtime.reconcile_turn("turn-2")

    assert first.session_binding == second.session_binding
    assert first.turn_binding.session_binding_id == first.session_binding.binding_id
    assert second.turn_binding.session_binding_id == second.session_binding.binding_id
    assert first.turn_binding != second.turn_binding
    assert runtime.session_binding_count == 1
    assert runtime.turn_binding_count == 2


async def test_duplicate_turn_with_different_input_is_permanent_conflict() -> None:
    """稳定 turn_run_id 不得被不同输入重新解释。"""
    runtime = FakeResearchAgentRuntime()
    request = _request()
    await _collect(runtime.execute_turn(request))
    conflicting = RuntimeTurnRequest(
        session_id=request.session_id,
        turn_run_id=request.turn_run_id,
        user_message_id=request.user_message_id,
        user_message_content="不同输入",
        context_snapshot=request.context_snapshot,
        policy_snapshot=request.policy_snapshot,
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(runtime.execute_turn(conflicting))

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_turn_conflict"
    assert runtime.execution_start_count == 1


async def test_cancel_stops_new_events_and_blocks_result() -> None:
    """取消后流不再产生新事件，且不能收集成功结果。"""
    runtime = FakeResearchAgentRuntime()
    stream = runtime.execute_turn(_request())

    first_event = await anext(stream)
    await runtime.cancel_turn("turn-1")
    remaining = [event async for event in stream]
    status = await runtime.reconcile_turn("turn-1")

    assert first_event.kind is RuntimeEventKind.BOUND
    assert remaining == []
    assert status.state is RuntimeExecutionState.CANCELLED
    assert status.last_event_sequence == 1
    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await runtime.collect_turn_result("turn-1")
    assert exc_info.value.kind is RuntimeErrorKind.CANCELLED


async def test_interrupt_resume_reuses_execution_and_collects_once() -> None:
    """恢复沿用原 Execution/Checkpoint，不创建第二次执行。"""
    runtime = FakeResearchAgentRuntime(interrupt_turn_ids=frozenset({"turn-1"}))
    request = _request()

    execute_events = await _collect(runtime.execute_turn(request))
    waiting = await runtime.reconcile_turn("turn-1")
    resume_events = await _collect(
        runtime.resume_turn(RuntimeResumeRequest(turn_run_id="turn-1", response="继续"))
    )
    completed = await runtime.reconcile_turn("turn-1")
    repeated_resume = await _collect(
        runtime.resume_turn(RuntimeResumeRequest(turn_run_id="turn-1", response="继续"))
    )

    assert execute_events[-1].kind is RuntimeEventKind.INTERRUPTED
    assert waiting.state is RuntimeExecutionState.INTERRUPTED
    assert resume_events == repeated_resume
    assert completed.state is RuntimeExecutionState.SUCCEEDED
    assert waiting.turn_binding == completed.turn_binding
    assert runtime.execution_start_count == 1
    assert (await runtime.collect_turn_result("turn-1")).assistant_content


async def test_unknown_and_invalid_operations_have_explicit_error_kinds() -> None:
    """未知 Turn 与非法恢复分别返回永久 not-found/conflict。"""
    runtime = FakeResearchAgentRuntime()

    with pytest.raises(ResearchAgentRuntimeError) as missing:
        await runtime.cancel_turn("missing")
    assert missing.value.kind is RuntimeErrorKind.PERMANENT
    assert missing.value.code == "runtime_turn_not_found"

    await _collect(runtime.execute_turn(_request()))
    with pytest.raises(ResearchAgentRuntimeError) as invalid_resume:
        await _collect(
            runtime.resume_turn(RuntimeResumeRequest(turn_run_id="turn-1", response=None))
        )
    assert invalid_resume.value.kind is RuntimeErrorKind.PERMANENT
    assert invalid_resume.value.code == "runtime_turn_not_interrupted"
