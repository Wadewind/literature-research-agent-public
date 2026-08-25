"""真实 create_deep_agent Adapter 的离线行为测试。"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.memory import MemorySaver

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeExecutionState,
    RuntimeTurnRequest,
)
from literature_agent.domain.research_agent import (
    create_context_snapshot,
    create_policy_snapshot,
)
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    DeepAgentsResearchAgentRuntime,
)
from tests.fakes.deep_agent_model import ScriptedDeepAgentChatModel


def _request(
    *,
    session_id: str = "session-1",
    turn_run_id: str = "turn-1",
    allowed_tool_names: tuple[str, ...] = ("record_research_step",),
    max_tool_calls: int = 2,
) -> RuntimeTurnRequest:
    message_id = f"message-{turn_run_id}"
    context = create_context_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id=session_id,
        turn_run_id=turn_run_id,
        user_message_id=message_id,
        history_through_sequence=0,
        review_output_id="review-output-1",
    )
    policy = create_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id=session_id,
        turn_run_id=turn_run_id,
        allowed_tool_names=allowed_tool_names,
        max_model_calls=8,
        max_tool_calls=max_tool_calls,
    )
    return RuntimeTurnRequest(
        session_id=session_id,
        turn_run_id=turn_run_id,
        user_message_id=message_id,
        user_message_content=(
            f"{turn_run_id}：第一轮研究" if turn_run_id == "turn-1" else "第二轮继续"
        ),
        context_snapshot=context,
        policy_snapshot=policy,
    )


async def _collect(stream: AsyncIterator[RuntimeEvent]) -> list[RuntimeEvent]:
    return [event async for event in stream]


async def _invoke_runtime_operation(
    runtime: DeepAgentsResearchAgentRuntime,
    request: RuntimeTurnRequest,
    operation: str,
) -> None:
    if operation == "execute":
        await _collect(runtime.execute_turn(request))
    elif operation == "reconcile":
        await runtime.reconcile_turn(request.turn_run_id)
    else:
        await runtime.collect_turn_result(request.turn_run_id)


class _FailingListCheckpointer(MemorySaver):
    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        del config, filter, before, limit
        raise RuntimeError("不得泄漏的 psycopg Secret")
        yield  # pragma: no cover - 保持异步迭代器签名


class _CancelledListCheckpointer(MemorySaver):
    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        del config, filter, before, limit
        raise asyncio.CancelledError
        yield  # pragma: no cover - 保持异步迭代器签名


class _FailingStateReadCheckpointer(MemorySaver):
    fail_state_read = False

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        if self.fail_state_read:
            raise RuntimeError("不得泄漏的 serializer Secret")
        return await super().aget_tuple(config)


async def test_real_adapter_replays_checkpoint_without_duplicate_model_or_tool() -> None:
    model = ScriptedDeepAgentChatModel()
    tool_calls: list[str] = []

    @tool
    def record_research_step(note: str) -> str:
        """记录一次确定性的研究步骤。"""
        tool_calls.append(note)
        return "recorded"

    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        tools=(record_research_step,),
        checkpointer=MemorySaver(),
        summarization_trigger=("messages", 100),
    )
    request = _request()

    first_events = await _collect(runtime.execute_turn(request))
    first_status = await runtime.reconcile_turn(request.turn_run_id)
    first_result = await runtime.collect_turn_result(request.turn_run_id)
    first_model_calls = model.model_call_count
    repeated_events = await _collect(runtime.execute_turn(request))

    assert first_events == repeated_events
    assert first_status.state is RuntimeExecutionState.SUCCEEDED
    assert first_status.result_available is True
    assert first_status.turn_binding.runtime_checkpoint_id
    assert first_result.assistant_content == "第一轮分析完成。"
    assert model.model_call_count == first_model_calls
    assert tool_calls == ["第一轮受控记录"]
    assert all("task" not in names and "execute" not in names for names in model.visible_tool_names)


async def test_same_thread_appends_only_new_message_and_native_summary_offloads_history() -> None:
    model = ScriptedDeepAgentChatModel()

    @tool
    def record_research_step(note: str) -> str:
        """返回确定性的研究步骤结果。"""
        return f"recorded:{note}"

    checkpointer = MemorySaver()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        tools=(record_research_step,),
        checkpointer=checkpointer,
        summarization_trigger=("messages", 3),
        summarization_keep=("messages", 1),
    )

    await _collect(runtime.execute_turn(_request()))
    await _collect(runtime.execute_turn(_request(turn_run_id="turn-2")))
    first = await runtime.reconcile_turn("turn-1")
    second = await runtime.reconcile_turn("turn-2")
    second_result = await runtime.collect_turn_result("turn-2")
    latest_tuple = await checkpointer.aget_tuple(
        {
            "configurable": {
                "thread_id": second.session_binding.runtime_thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": second.turn_binding.runtime_checkpoint_id,
            }
        }
    )

    assert first.session_binding.runtime_thread_id == second.session_binding.runtime_thread_id
    assert first.turn_binding.runtime_execution_id != second.turn_binding.runtime_execution_id
    assert second_result.assistant_content == "第二轮基于同一 Thread 的压缩上下文继续完成。"
    assert model.summary_call_count >= 1
    assert latest_tuple is not None
    latest = (await runtime._graph.aget_state(latest_tuple.config)).values  # noqa: SLF001
    raw_human_ids = [
        message.id for message in latest["messages"] if isinstance(message, HumanMessage)
    ]
    assert raw_human_ids.count("message-turn-1") == 1
    assert raw_human_ids.count("message-turn-2") == 1
    assert latest["_summarization_event"]["cutoff_index"] > 0
    history_files = [path for path in latest["files"] if path.startswith("/conversation_history/")]
    assert len(history_files) == 1
    assert any(
        "已压缩：第一轮完成了受控研究记录" in item
        for item in model.observed_message_text[-1]
    )
    assert all("turn-1：第一轮研究" not in item for item in model.observed_message_text[-1])


async def test_cancel_after_started_prevents_any_new_model_or_tool_call() -> None:
    model = ScriptedDeepAgentChatModel()

    @tool
    def record_research_step(note: str) -> str:
        """不应在取消后被调用。"""
        pytest.fail(f"取消后不应调用 Tool: {note}")

    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        tools=(record_research_step,),
        checkpointer=MemorySaver(),
    )
    stream = runtime.execute_turn(_request())

    assert (await anext(stream)).kind is RuntimeEventKind.BOUND
    assert (await anext(stream)).kind is RuntimeEventKind.STARTED
    cancelled = await runtime.cancel_turn("turn-1")
    remaining = [event async for event in stream]

    assert remaining == []
    assert cancelled.state is RuntimeExecutionState.CANCELLED
    assert cancelled.turn_binding.runtime_checkpoint_id != "checkpoint-not-created"
    assert await runtime.reconcile_turn("turn-1") == cancelled
    assert model.model_call_count == 0
    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await runtime.collect_turn_result("turn-1")
    assert exc_info.value.kind is RuntimeErrorKind.CANCELLED


async def test_existing_turn_rejects_conflicting_input_without_new_execution() -> None:
    model = ScriptedDeepAgentChatModel()
    runtime = DeepAgentsResearchAgentRuntime(model=model, checkpointer=MemorySaver())
    request = _request(turn_run_id="turn-2")
    await _collect(runtime.execute_turn(request))
    conflicting = RuntimeTurnRequest(
        session_id=request.session_id,
        turn_run_id=request.turn_run_id,
        user_message_id=request.user_message_id,
        user_message_content="不同输入",
        context_snapshot=request.context_snapshot,
        policy_snapshot=request.policy_snapshot,
    )
    calls_before_conflict = model.model_call_count

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(runtime.execute_turn(conflicting))

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_turn_conflict"
    assert model.model_call_count == calls_before_conflict


async def test_model_failure_is_normalized_and_remains_reconcilable() -> None:
    class FailingModel(ScriptedDeepAgentChatModel):
        async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise RuntimeError("不应泄漏的 Provider 原始失败")

    runtime = DeepAgentsResearchAgentRuntime(model=FailingModel(), checkpointer=MemorySaver())

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(runtime.execute_turn(_request(turn_run_id="turn-2")))

    assert exc_info.value.kind is RuntimeErrorKind.TEMPORARY
    assert exc_info.value.code == "runtime_execution_failed"
    assert "Provider" not in exc_info.value.safe_message
    failed = await runtime.reconcile_turn("turn-2")
    assert failed.state is RuntimeExecutionState.FAILED
    assert failed.turn_binding.runtime_checkpoint_id != "checkpoint-not-created"


async def test_hidden_custom_tool_is_also_denied_at_execution_boundary() -> None:
    model = ScriptedDeepAgentChatModel()
    tool_calls: list[str] = []

    @tool
    def record_research_step(note: str) -> str:
        """未授权时不能执行。"""
        tool_calls.append(note)
        return "recorded"

    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        tools=(record_research_step,),
        checkpointer=MemorySaver(),
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(
            runtime.execute_turn(_request(allowed_tool_names=(), max_tool_calls=0))
        )

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_tool_not_allowed"
    assert tool_calls == []
    assert all(not names for names in model.visible_tool_names)
    assert (await runtime.reconcile_turn("turn-1")).state is RuntimeExecutionState.FAILED


async def test_policy_allows_only_the_selected_filesystem_tool() -> None:
    model = ScriptedDeepAgentChatModel()
    runtime = DeepAgentsResearchAgentRuntime(model=model, checkpointer=MemorySaver())

    result = await _collect(
        runtime.execute_turn(
            _request(turn_run_id="turn-2", allowed_tool_names=("read_file",))
        )
    )

    assert result[-1].kind is RuntimeEventKind.COMPLETED
    assert model.visible_tool_names
    assert all(names == ("read_file",) for names in model.visible_tool_names)


async def test_execute_remains_forbidden_even_if_registered_and_policy_named() -> None:
    class ExecuteRequestingModel(ScriptedDeepAgentChatModel):
        def _next_message(self, messages: list[Any]) -> AIMessage:
            del messages
            self.model_call_count += 1
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "blocked"},
                        "id": "execute-call-1",
                        "type": "tool_call",
                    }
                ],
            )

    calls: list[str] = []

    @tool
    def execute(command: str) -> str:
        """本切片不得执行的同名自定义工具。"""
        calls.append(command)
        return "executed"

    model = ExecuteRequestingModel()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        tools=(execute,),
        checkpointer=MemorySaver(),
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(
            runtime.execute_turn(
                _request(allowed_tool_names=("execute",), max_tool_calls=1)
            )
        )

    assert exc_info.value.code == "runtime_tool_not_allowed"
    assert calls == []
    assert all("execute" not in names for names in model.visible_tool_names)


async def test_exact_model_harness_profile_does_not_pollute_same_provider() -> None:
    restricted_model = ScriptedDeepAgentChatModel(model_name="restricted-model")
    runtime = DeepAgentsResearchAgentRuntime(
        model=restricted_model,
        checkpointer=MemorySaver(),
    )
    await _collect(runtime.execute_turn(_request(turn_run_id="turn-2")))
    assert all("task" not in names for names in restricted_model.visible_tool_names)

    other_model = ScriptedDeepAgentChatModel(model_name="unregistered-model")
    unrestricted_graph = create_deep_agent(
        model=other_model,
        tools=[],
        subagents=[],
        checkpointer=MemorySaver(),
    )
    await unrestricted_graph.ainvoke(
        {"messages": [HumanMessage(content="第二轮", id="unregistered-message")]},
        {"configurable": {"thread_id": "unregistered-thread"}},
    )

    assert any("task" in names for names in other_model.visible_tool_names)


def test_harness_profile_rejects_missing_exact_model_identity() -> None:
    class MissingModelIdentity(ScriptedDeepAgentChatModel):
        def _get_ls_params(self, **kwargs: Any) -> dict[str, Any]:
            params = super()._get_ls_params(**kwargs)
            params.pop("ls_model_name")
            return params

    with pytest.raises(ValueError, match="ls_provider/ls_model_name"):
        DeepAgentsResearchAgentRuntime(
            model=MissingModelIdentity(),
            checkpointer=MemorySaver(),
        )


@pytest.mark.parametrize("operation", ["execute", "reconcile", "collect"])
async def test_checkpoint_listing_failure_is_safely_normalized(operation: str) -> None:
    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=_FailingListCheckpointer(),
    )
    request = _request(turn_run_id="turn-2")

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _invoke_runtime_operation(runtime, request, operation)

    assert exc_info.value.kind is RuntimeErrorKind.TEMPORARY
    assert exc_info.value.code == "runtime_checkpoint_unavailable"
    assert exc_info.value.safe_message == "Runtime Checkpoint 暂时不可用"
    assert "Secret" not in str(exc_info.value)


@pytest.mark.parametrize("operation", ["execute", "reconcile", "collect"])
async def test_checkpoint_state_failure_is_safely_normalized(operation: str) -> None:
    checkpointer = _FailingStateReadCheckpointer()
    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=checkpointer,
    )
    request = _request(turn_run_id="turn-2")
    await _collect(runtime.execute_turn(request))
    checkpointer.fail_state_read = True

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _invoke_runtime_operation(runtime, request, operation)

    assert exc_info.value.kind is RuntimeErrorKind.TEMPORARY
    assert exc_info.value.code == "runtime_checkpoint_unreadable"
    assert exc_info.value.safe_message == "Runtime Checkpoint 状态暂时不可读取"
    assert "Secret" not in str(exc_info.value)


async def test_checkpoint_cancellation_is_not_normalized_as_infrastructure_failure() -> None:
    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=_CancelledListCheckpointer(),
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.reconcile_turn("turn-2")
