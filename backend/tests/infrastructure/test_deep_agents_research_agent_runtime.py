"""真实 create_deep_agent Adapter 的离线行为测试。"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from deepagents import create_deep_agent
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.memory import MemorySaver
from mcp.types import Tool
from pydantic import Field

from literature_agent.application.ports.agent_usage_control import RuntimeBudget
from literature_agent.application.ports.project_research_context import (
    ProjectContextToolResult,
    ProjectResearchContextError,
)
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeExecutionState,
    RuntimeResumeRequest,
    RuntimeTurnRequest,
)
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
    RuntimeExecutionControlService,
)
from literature_agent.domain.agent_usage import (
    AgentToolCallStatus,
    create_agent_tool_call,
    create_agent_turn_usage,
)
from literature_agent.domain.mcp_configuration import (
    McpPolicyRef,
    McpPolicyToolRef,
    canonical_json_hash,
)
from literature_agent.domain.research_agent import (
    create_context_snapshot,
    create_policy_snapshot,
    create_project_research_workspace_policy_snapshot,
)
from literature_agent.domain.run import RunStatus, create_run
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt
from literature_agent.domain.runtime_execution import RuntimeControlState, RuntimeExecutionPermit
from literature_agent.domain.tool_execution import ToolErrorKind
from literature_agent.infrastructure.agent import (
    deep_agents_research_agent_runtime as runtime_module,
)
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    DeepAgentsResearchAgentRuntime,
    _checkpoint_id,
    _opaque_id,
    _PersistentUsageMiddleware,
    _replayable_tool_names,
    _request_hash,
    _RuntimeToolPolicyMiddleware,
    _stream_with_deadline,
    _tool_schema_hash,
    _TurnContext,
)
from tests.fakes.deep_agent_model import ScriptedDeepAgentChatModel
from tests.fakes.fake_attempt_repository import FakeAttemptRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository
from tests.fakes.fake_runtime_execution_repository import FakeRuntimeExecutionRepository

_LEASE_NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)


class _UsageControl:
    def __init__(self) -> None:
        self.usage = create_agent_turn_usage(
            turn_run_id="turn-1",
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            policy_snapshot_id="policy-1",
            max_model_calls=8,
            max_tool_calls=12,
            now=_LEASE_NOW,
        ).start(now=datetime.now(UTC))
        self.model_ordinals: list[int] = []
        self.calls = {}
        self.succeeded_calls: list[str] = []
        self.failed_calls: list[tuple[str, str]] = []

    async def start_turn(self, turn_run_id):
        assert turn_run_id == "turn-1"
        assert self.usage.deadline_at is not None
        return RuntimeBudget(
            deadline_at=self.usage.deadline_at,
            tool_timeout_seconds=30,
            execute_timeout_seconds=60,
            max_tool_output_bytes=64 * 1024,
            max_input_tokens_per_model_call=60_000,
            max_output_tokens_per_model_call=2_048,
        )

    async def reserve_model_call(self, turn_run_id, ordinal, *, approximate_input_tokens):
        assert turn_run_id == "turn-1"
        assert approximate_input_tokens < 60_000
        self.model_ordinals.append(ordinal)
        return self.usage

    async def record_model_usage(self, *args, **kwargs):
        return None

    async def reserve_tool_call(self, turn_run_id, request):
        existing = self.calls.get(request.invocation_id)
        if existing is not None:
            return existing
        value = create_agent_tool_call(
            turn_run_id=turn_run_id,
            invocation_id=request.invocation_id,
            tool_name=request.tool_name,
            tool_version="project-context.v1",
            input_schema_hash=request.input_schema_hash,
            args_hash=request.args_hash,
            input_size_bytes=request.input_size_bytes,
        )
        self.calls[request.invocation_id] = value
        return value

    async def start_tool_call(self, turn_run_id, reservation_key):
        value = next(v for v in self.calls.values() if v.reservation_key == reservation_key)
        started = value.start()
        self.calls[value.invocation_id] = started
        return started

    async def succeed_tool_call(
        self, turn_run_id, reservation_key, *, output_size_bytes, result_hash
    ):
        value = next(v for v in self.calls.values() if v.reservation_key == reservation_key)
        if value.status is AgentToolCallStatus.SUCCEEDED:
            assert value.output_size_bytes == output_size_bytes
            assert value.result_hash == result_hash
            return value
        succeeded = value.succeed(output_size_bytes=output_size_bytes, result_hash=result_hash)
        self.calls[value.invocation_id] = succeeded
        self.succeeded_calls.append(value.invocation_id)
        return succeeded

    async def fail_tool_call(
        self,
        turn_run_id,
        reservation_key,
        *,
        error_code,
        safe_message,
    ):
        del turn_run_id, safe_message
        value = next(v for v in self.calls.values() if v.reservation_key == reservation_key)
        self.failed_calls.append((value.invocation_id, error_code))
        return value


async def _controlled_runtime_dependencies(turn_run_id: str):
    clock = [_LEASE_NOW]
    run_repo = FakeRunRepository()
    run = create_run(project_id="project-1", owner_id="owner-1", run_type="agent_turn")
    run = replace(run.transition_to(RunStatus.RUNNING), run_id=turn_run_id)
    await run_repo.add(run)
    attempt_repo = FakeAttemptRepository()
    first_attempt = RunAttempt(
        attempt_id="attempt-1",
        run_id=turn_run_id,
        attempt_number=1,
        worker_id="worker-1",
        status=AttemptStatus.RUNNING,
        started_at=clock[0],
        heartbeat_at=clock[0],
    )
    await attempt_repo.add(first_attempt)
    execution_repo = FakeRuntimeExecutionRepository()
    control = RuntimeExecutionControlService(
        session_factory=fake_session,
        run_repo_factory=lambda _: run_repo,
        attempt_repo_factory=lambda _: attempt_repo,
        execution_repo_factory=lambda _: execution_repo,
        lease_seconds=30,
        clock=lambda: clock[0],
    )
    return control, clock, attempt_repo, execution_repo


class _ProjectContext:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str]] = []
        self.matrix_calls: list[str] = []

    async def search_project_chunks(
        self, turn_run_id: str, *, query: str
    ) -> ProjectContextToolResult:
        self.search_calls.append((turn_run_id, query))
        return ProjectContextToolResult(
            effect_id="effect-search-1",
            tool_name="search_project_chunks",
            payload={
                "items": [
                    {
                        "evidence_id": "evidence-agent-1",
                        "excerpt": "有界证据",
                    }
                ],
                "returned_count": 1,
                "truncated": False,
            },
            result_hash="a" * 64,
        )

    async def read_review_evidence_matrix(self, turn_run_id: str) -> ProjectContextToolResult:
        self.matrix_calls.append(turn_run_id)
        return ProjectContextToolResult(
            effect_id="effect-matrix-1",
            tool_name="read_review_evidence_matrix",
            payload={"rows": [], "returned_count": 0, "truncated": False},
            result_hash="b" * 64,
        )


class _ProjectToolModel(ScriptedDeepAgentChatModel):
    visible_tool_schemas: list[dict[str, Any]] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: list[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        self.visible_tool_schemas.append(
            {item.name: item.tool_call_schema.model_json_schema() for item in tools}
        )
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def _next_message(self, messages: list[Any]) -> AIMessage:
        self.model_call_count += 1
        if self._tool_requested:
            return AIMessage(
                content=(
                    "## 研究结论\n"
                    "- 该方法得到本地论文支持[evidence:evidence-agent-1]\n"
                    "仍需进一步验证。"
                )
            )
        self._tool_requested = True
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_project_chunks",
                    "args": {"query": "图神经网络"},
                    "id": "project-search-1",
                    "type": "tool_call",
                }
            ],
        )


class _MatrixToolModel(_ProjectToolModel):
    def _next_message(self, messages: list[Any]) -> AIMessage:
        self.model_call_count += 1
        if self._tool_requested:
            return AIMessage(content="当前授权上下文证据不足。")
        self._tool_requested = True
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_review_evidence_matrix",
                    "args": {},
                    "id": "project-matrix-1",
                    "type": "tool_call",
                }
            ],
        )


class _ProjectFirstThenNaturalReplyModel(_ProjectToolModel):
    """首轮读取项目证据，次轮只返回不带引用的普通说明。"""

    def _next_message(self, messages: list[Any]) -> AIMessage:
        self.model_call_count += 1
        latest_human_index = max(
            index for index, message in enumerate(messages) if isinstance(message, HumanMessage)
        )
        latest_human = messages[latest_human_index]
        if "第二轮继续" in latest_human.text:
            return AIMessage(content="请明确要保存的内容，我会生成 TXT 文件。")
        used_project_tool = any(
            isinstance(message, ToolMessage) and message.name == "search_project_chunks"
            for message in messages[latest_human_index + 1 :]
        )
        if used_project_tool:
            return AIMessage(content="首轮项目结论 [evidence:evidence-agent-1]")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_project_chunks",
                    "args": {"query": "首轮研究"},
                    "id": "project-first-turn",
                    "type": "tool_call",
                }
            ],
        )


class _FailingMatrixContext(_ProjectContext):
    async def read_review_evidence_matrix(self, turn_run_id: str) -> ProjectContextToolResult:
        self.matrix_calls.append(turn_run_id)
        raise ProjectResearchContextError(
            "project_context_matrix_unavailable",
            "Matrix 暂时不可用",
            ToolErrorKind.TEMPORARY,
        )


class _ExecuteSandbox(BaseSandbox):
    """只为离线预算测试提供确定性 execute。"""

    def __init__(self) -> None:
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "sandbox-budget-test"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        self.commands.append(command)
        return ExecuteResponse(output="ok", exit_code=0, truncated=False)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, content=b"") for path in paths]


class _ProjectThenExecuteModel(_ProjectToolModel):
    def _next_message(self, messages: list[Any]) -> AIMessage:
        del messages
        self.model_call_count += 1
        if self.model_call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_project_chunks",
                        "args": {"query": "统一预算"},
                        "id": "project-budget-call",
                        "type": "tool_call",
                    }
                ],
            )
        if self.model_call_count == 2:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "python -c 'print(1)'"},
                        "id": "execute-budget-call",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="当前授权上下文证据不足。")


def _request(
    *,
    session_id: str = "session-1",
    turn_run_id: str = "turn-1",
    allowed_tool_names: tuple[str, ...] = ("record_research_step",),
    max_tool_calls: int = 2,
    max_model_calls: int = 8,
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
        max_model_calls=max_model_calls,
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


async def test_unified_tool_budget_counts_project_and_execute_once_each() -> None:
    model = _ProjectThenExecuteModel()
    context = _ProjectContext()
    sandbox = _ExecuteSandbox()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        checkpointer=MemorySaver(),
        backend=sandbox,
        project_context=context,
    )

    events = await _collect(
        runtime.execute_turn(
            _request(
                allowed_tool_names=("search_project_chunks", "execute"),
                max_tool_calls=2,
            )
        )
    )

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert context.search_calls == [("turn-1", "统一预算")]
    assert sandbox.commands == ["python -c 'print(1)'"]


async def test_persistent_usage_wraps_model_and_project_tool_boundaries() -> None:
    usage = _UsageControl()
    middleware = _PersistentUsageMiddleware(usage)

    @tool
    async def search_project_chunks(query: str) -> str:
        """离线 Project Context effect cache 夹具。"""
        return query

    context = _TurnContext(
        turn_run_id="turn-1",
        allowed_tool_names=frozenset({"search_project_chunks"}),
        max_model_calls=8,
        max_tool_calls=12,
        replayable_tool_names=frozenset({"search_project_chunks"}),
        runtime_permit=None,
    )
    runtime = ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="project-search-1",
        store=None,
        tools=[search_project_chunks],
    )
    request = ToolCallRequest(
        tool_call={
            "name": "search_project_chunks",
            "args": {"query": "图神经网络"},
            "id": "project-search-1",
            "type": "tool_call",
        },
        tool=search_project_chunks,
        state={},
        runtime=runtime,
    )
    effects: list[str] = []

    async def cached_handler(_: ToolCallRequest) -> ToolMessage:
        effects.append("effect-or-cache-read")
        return ToolMessage(content='{"result_hash":"a"}', tool_call_id="project-search-1")

    await middleware.awrap_tool_call(request, cached_handler)
    await middleware.awrap_tool_call(request, cached_handler)

    assert usage.calls["project-search-1"].status is AgentToolCallStatus.SUCCEEDED
    assert effects == ["effect-or-cache-read", "effect-or-cache-read"]


async def test_persistent_usage_never_reexecutes_terminal_execute_effect() -> None:
    usage = _UsageControl()
    middleware = _PersistentUsageMiddleware(usage)

    @tool
    async def execute(command: str) -> str:
        """离线 execute 夹具。"""
        return command

    runtime = ToolRuntime(
        state={},
        context=_TurnContext(
            turn_run_id="turn-1",
            allowed_tool_names=frozenset({"execute"}),
            max_model_calls=8,
            max_tool_calls=12,
            runtime_permit=None,
        ),
        config={},
        stream_writer=lambda _: None,
        tool_call_id="execute-1",
        store=None,
        tools=[execute],
    )
    request = ToolCallRequest(
        tool_call={
            "name": "execute",
            "args": {"command": "python -c 'print(1)'"},
            "id": "execute-1",
            "type": "tool_call",
        },
        tool=execute,
        state={},
        runtime=runtime,
    )
    effects = 0

    async def handler(_: ToolCallRequest) -> ToolMessage:
        nonlocal effects
        effects += 1
        return ToolMessage(content="ok", tool_call_id="execute-1")

    await middleware.awrap_tool_call(request, handler)
    with pytest.raises(ResearchAgentRuntimeError) as replay:
        await middleware.awrap_tool_call(request, handler)

    assert replay.value.code == "agent_tool_effect_not_replayable"
    assert effects == 1


async def test_persistent_usage_rejects_non_finite_tool_args_before_effect() -> None:
    usage = _UsageControl()
    middleware = _PersistentUsageMiddleware(usage)

    @tool
    async def execute(value: float) -> str:
        """离线 execute 夹具。"""
        return str(value)

    runtime = ToolRuntime(
        state={},
        context=_TurnContext(
            turn_run_id="turn-1",
            allowed_tool_names=frozenset({"execute"}),
            max_model_calls=8,
            max_tool_calls=12,
            runtime_permit=None,
        ),
        config={},
        stream_writer=lambda _: None,
        tool_call_id="execute-nan",
        store=None,
        tools=[execute],
    )
    request = ToolCallRequest(
        tool_call={
            "name": "execute",
            "args": {"value": float("nan")},
            "id": "execute-nan",
            "type": "tool_call",
        },
        tool=execute,
        state={},
        runtime=runtime,
    )

    with pytest.raises(ResearchAgentRuntimeError) as rejected:
        await middleware.awrap_tool_call(
            request,
            lambda _: pytest.fail("参数拒绝后不得执行 Tool"),
        )
    assert rejected.value.code == "runtime_tool_args_invalid"


async def test_expired_runtime_wall_budget_does_not_advance_graph_stream() -> None:
    advanced = False

    async def graph_stream():
        nonlocal advanced
        advanced = True
        yield {"model": "must-not-run"}

    budget = RuntimeBudget(
        deadline_at=datetime.now(UTC) - timedelta(milliseconds=1),
        tool_timeout_seconds=30,
        execute_timeout_seconds=60,
        max_tool_output_bytes=64 * 1024,
        max_input_tokens_per_model_call=60_000,
        max_output_tokens_per_model_call=2_048,
    )
    with pytest.raises(ResearchAgentRuntimeError) as expired:
        await anext(_stream_with_deadline(graph_stream(), budget))

    assert expired.value.code == "agent_turn_deadline_exceeded"
    assert advanced is False


async def test_stream_deadline_does_not_leave_timer_active_while_consumer_handles_item() -> None:
    async def graph_stream():
        yield "first"
        yield "second"

    budget = RuntimeBudget(
        deadline_at=datetime.now(UTC) + timedelta(milliseconds=40),
        tool_timeout_seconds=30,
        execute_timeout_seconds=60,
        max_tool_output_bytes=64 * 1024,
        max_input_tokens_per_model_call=60_000,
        max_output_tokens_per_model_call=2_048,
    )
    wrapped = _stream_with_deadline(graph_stream(), budget)

    assert await anext(wrapped) == "first"
    # 消费者处理期间没有悬挂 asyncio.timeout 来取消当前 Task；绝对 deadline
    # 仍然流逝，下一次推进会在接触底层 stream 前拒绝。
    await asyncio.sleep(0.06)
    with pytest.raises(ResearchAgentRuntimeError) as expired:
        await anext(wrapped)
    assert expired.value.code == "agent_turn_deadline_exceeded"


def test_project_tool_actual_schemas_match_frozen_policy_refs() -> None:
    policy = create_project_research_workspace_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
    )
    expected = {ref.name: ref.input_schema_hash for ref in policy.tool_refs}
    actual = {
        value.name: _tool_schema_hash(value)
        for value in DeepAgentsResearchAgentRuntime._project_context_tools(  # noqa: SLF001
            _ProjectContext()
        )
    }

    assert actual == {name: expected[name] for name in actual}


def test_deepagents_builtin_tool_schemas_match_frozen_policy_refs() -> None:
    policy = create_project_research_workspace_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
    )
    expected = {ref.name: ref.input_schema_hash for ref in policy.tool_refs}
    file_names = ["ls", "read_file", "write_file", "edit_file", "glob", "grep"]
    tools = FilesystemMiddleware(
        backend=_ExecuteSandbox(),
        tools=[*file_names, "execute"],
        max_execute_timeout=60,
    ).tools

    actual = {value.name: _tool_schema_hash(value) for value in tools}
    assert actual == {name: expected[name] for name in actual}


def test_converted_mcp_tool_hashes_raw_input_schema_not_description() -> None:
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    definition = Tool(
        name="search_papers",
        description="非空描述不能进入 MCP inputSchema 契约哈希",
        inputSchema=input_schema,
    )
    converted = convert_mcp_tool_to_langchain_tool(
        cast(Any, object()),
        definition,
        server_name="arxiv-search",
        tool_name_prefix=True,
    )
    tool_ref = McpPolicyToolRef(converted.name, canonical_json_hash(input_schema))
    mcp_ref = McpPolicyRef(
        profile_id="profile-1",
        profile_revision=1,
        catalog_id="arxiv-search",
        version="0.6.2",
        config_hash="a" * 64,
        tools=(tool_ref,),
    )
    policy = create_project_research_workspace_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        mcp_refs=(mcp_ref,),
    )

    expected = next(ref for ref in policy.tool_refs if ref.name == converted.name)
    assert _tool_schema_hash(converted) == expected.input_schema_hash
    assert "description" in cast(dict[str, Any], converted.tool_call_schema)


async def test_only_policy_declared_mcp_tool_is_effect_cache_replayable() -> None:
    usage = _UsageControl()
    middleware = _PersistentUsageMiddleware(usage)
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    tool_ref = McpPolicyToolRef("arxiv-search_search_papers", canonical_json_hash(schema))
    mcp_ref = McpPolicyRef(
        profile_id="profile-1",
        profile_revision=1,
        catalog_id="arxiv-search",
        version="0.6.2",
        config_hash="a" * 64,
        tools=(tool_ref,),
    )
    policy = create_project_research_workspace_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        mcp_refs=(mcp_ref,),
    )

    @tool("arxiv-search_search_papers", args_schema=schema)
    async def catalogued(query: str) -> str:
        """Catalog 固定 MCP Tool。"""
        return query

    context = _TurnContext(
        turn_run_id="turn-1",
        allowed_tool_names=frozenset(policy.allowed_tool_names),
        max_model_calls=8,
        max_tool_calls=12,
        replayable_tool_names=_replayable_tool_names(policy),
    )
    runtime = ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="mcp-call-1",
        store=None,
        tools=[catalogued],
    )
    request = ToolCallRequest(
        tool_call={
            "name": catalogued.name,
            "args": {"query": "budget"},
            "id": "mcp-call-1",
            "type": "tool_call",
        },
        tool=catalogued,
        state={},
        runtime=runtime,
    )
    effects = 0

    async def handler(_: ToolCallRequest) -> ToolMessage:
        nonlocal effects
        effects += 1
        return ToolMessage(content="cached", tool_call_id="mcp-call-1")

    await middleware.awrap_tool_call(request, handler)
    await middleware.awrap_tool_call(request, handler)
    assert effects == 2


async def test_prefix_lookalike_custom_tool_is_not_effect_cache_replayable() -> None:
    usage = _UsageControl()
    middleware = _PersistentUsageMiddleware(usage)

    @tool("arxiv_unregistered_custom")
    async def lookalike(query: str) -> str:
        """名称相似但不属于冻结 MCP Policy。"""
        return query

    context = _TurnContext(
        turn_run_id="turn-1",
        allowed_tool_names=frozenset({lookalike.name}),
        max_model_calls=8,
        max_tool_calls=12,
    )
    runtime = ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="custom-call-1",
        store=None,
        tools=[lookalike],
    )
    request = ToolCallRequest(
        tool_call={
            "name": lookalike.name,
            "args": {"query": "budget"},
            "id": "custom-call-1",
            "type": "tool_call",
        },
        tool=lookalike,
        state={},
        runtime=runtime,
    )
    effects = 0

    async def handler(_: ToolCallRequest) -> ToolMessage:
        nonlocal effects
        effects += 1
        return ToolMessage(content="uncached", tool_call_id="custom-call-1")

    await middleware.awrap_tool_call(request, handler)
    with pytest.raises(ResearchAgentRuntimeError) as replay:
        await middleware.awrap_tool_call(request, handler)
    assert replay.value.code == "agent_tool_effect_not_replayable"
    assert effects == 1


async def test_post_tool_fence_failure_is_recorded_as_failure_not_success() -> None:
    class _ExecutionControl:
        checks = 0

        async def assert_active(self, permit) -> None:
            del permit
            self.checks += 1
            if self.checks == 2:
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost",
                    "Runtime Execution lease 已失效",
                    temporary=True,
                )

    usage = _UsageControl()
    policy = _RuntimeToolPolicyMiddleware(
        registered_tool_names=frozenset({"search_project_chunks"}),
        execution_control=cast(Any, _ExecutionControl()),
    )
    persistent = _PersistentUsageMiddleware(usage)

    @tool
    async def search_project_chunks(query: str) -> str:
        """离线 Project Context Tool。"""
        return query

    context = _TurnContext(
        turn_run_id="turn-1",
        allowed_tool_names=frozenset({search_project_chunks.name}),
        max_model_calls=8,
        max_tool_calls=12,
        replayable_tool_names=frozenset({search_project_chunks.name}),
        runtime_permit=RuntimeExecutionPermit("turn-1", "holder-1", "attempt-1", 1),
    )
    runtime = ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="guarded-call-1",
        store=None,
        tools=[search_project_chunks],
    )
    request = ToolCallRequest(
        tool_call={
            "name": search_project_chunks.name,
            "args": {"query": "fence"},
            "id": "guarded-call-1",
            "type": "tool_call",
        },
        tool=search_project_chunks,
        state={},
        runtime=runtime,
    )

    async def actual_handler(_: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="effect", tool_call_id="guarded-call-1")

    with pytest.raises(ResearchAgentRuntimeError) as lost:
        await persistent.awrap_tool_call(
            request,
            lambda inner: policy.awrap_tool_call(inner, actual_handler),
        )
    assert lost.value.code == "runtime_execution_lease_lost"
    assert usage.succeeded_calls == []
    assert usage.failed_calls == [("guarded-call-1", "runtime_execution_lease_lost")]


def test_persistent_usage_is_outside_runtime_policy_in_middleware_order(monkeypatch) -> None:
    captured: list[object] = []

    def capture_graph(**kwargs):
        captured.extend(kwargs["middleware"])
        return SimpleNamespace()

    monkeypatch.setattr(runtime_module, "create_deep_agent", capture_graph)
    DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=MemorySaver(),
        usage_control=_UsageControl(),
    )

    persistent_index = next(
        index
        for index, value in enumerate(captured)
        if isinstance(value, _PersistentUsageMiddleware)
    )
    policy_index = next(
        index
        for index, value in enumerate(captured)
        if isinstance(value, _RuntimeToolPolicyMiddleware)
    )
    assert persistent_index < policy_index


async def test_unified_tool_budget_rejects_execute_before_second_tool_effect() -> None:
    model = _ProjectThenExecuteModel()
    context = _ProjectContext()
    sandbox = _ExecuteSandbox()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        checkpointer=MemorySaver(),
        backend=sandbox,
        project_context=context,
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(
            runtime.execute_turn(
                _request(
                    allowed_tool_names=("search_project_chunks", "execute"),
                    max_tool_calls=1,
                )
            )
        )

    assert exc_info.value.code == "runtime_tool_call_limit_exceeded"
    assert context.search_calls == [("turn-1", "统一预算")]
    assert sandbox.commands == []


async def test_model_call_budget_is_reserved_before_the_main_agent_call() -> None:
    """额度为零时必须在 Provider 调用前 fail-closed。"""
    model = ScriptedDeepAgentChatModel()
    runtime = DeepAgentsResearchAgentRuntime(model=model, checkpointer=MemorySaver())

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(runtime.execute_turn(_request(max_model_calls=0)))

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_model_call_limit_exceeded"
    assert model.model_call_count == 0


async def test_model_call_budget_is_not_refunded_after_checkpointed_failure() -> None:
    """Tool 节点失败后的恢复必须沿 Checkpoint 保留主调用额度。"""

    tool_calls = 0

    @tool
    def record_research_step(note: str) -> str:
        """首次失败、恢复时成功的离线 Tool。"""
        nonlocal tool_calls
        tool_calls += 1
        if tool_calls == 1:
            raise RuntimeError("tool response lost")
        return note

    checkpointer = MemorySaver()
    request = _request(max_model_calls=1)
    failing_model = ScriptedDeepAgentChatModel()
    first = DeepAgentsResearchAgentRuntime(
        model=failing_model,
        tools=(record_research_step,),
        checkpointer=checkpointer,
    )

    with pytest.raises(ResearchAgentRuntimeError) as first_error:
        await _collect(first.execute_turn(request))
    assert first_error.value.kind is RuntimeErrorKind.TEMPORARY
    assert failing_model.model_call_count == 1
    assert tool_calls == 1

    recovered_model = ScriptedDeepAgentChatModel()
    recovered = DeepAgentsResearchAgentRuntime(
        model=recovered_model,
        tools=(record_research_step,),
        checkpointer=checkpointer,
    )
    with pytest.raises(ResearchAgentRuntimeError) as recovered_error:
        await _collect(
            recovered.resume_turn(
                RuntimeResumeRequest(
                    turn_run_id=request.turn_run_id,
                    response=None,
                    turn_request=request,
                )
            )
        )

    assert recovered_model.model_call_count == 0
    assert recovered_error.value.kind is RuntimeErrorKind.PERMANENT
    assert recovered_error.value.code == "runtime_model_call_limit_exceeded"
    assert tool_calls == 2


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


async def test_real_adapter_uses_sync_checkpoint_durability() -> None:
    """每个 Agent 图 Step 必须在下一 Step 前同步持久化。"""

    class _GraphProxy:
        def __init__(self, graph) -> None:
            self._graph = graph
            self.durabilities: list[str | None] = []

        def astream(self, *args, **kwargs):
            self.durabilities.append(kwargs.get("durability"))
            return self._graph.astream(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._graph, name)

    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(), checkpointer=MemorySaver()
    )
    proxy = _GraphProxy(runtime._graph)  # noqa: SLF001
    runtime._graph = proxy  # type: ignore[assignment]  # noqa: SLF001

    await _collect(runtime.execute_turn(_request(turn_run_id="turn-2", allowed_tool_names=())))

    assert proxy.durabilities == ["sync"]


async def test_workspace_finalizer_precedes_runtime_success_and_completed() -> None:
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, _, _, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    order: list[str] = []
    original_succeed = control.succeed

    async def finalize(turn_request: RuntimeTurnRequest) -> None:
        assert turn_request == request
        record = await control.get(request.turn_run_id)
        assert record is not None and record.state is RuntimeControlState.RUNNING
        order.append("snapshot")

    async def succeed(permit, checkpoint_id):
        order.append("control_succeed")
        return await original_succeed(permit, checkpoint_id)

    control.succeed = succeed  # type: ignore[method-assign]
    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=MemorySaver(),
        execution_control=control,
        runtime_owner_id="runtime-finalization-order",
        before_succeed=finalize,
    )

    events = await _collect(runtime.execute_turn(request))
    order.append("completed")

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert order == ["snapshot", "control_succeed", "completed"]


async def test_temporary_workspace_finalization_retries_succeeded_checkpoint() -> None:
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, _, _, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    saver = MemorySaver()
    model = ScriptedDeepAgentChatModel()
    calls = 0

    async def finalize(turn_request: RuntimeTurnRequest) -> None:
        nonlocal calls
        assert turn_request == request
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary storage failure")

    first = DeepAgentsResearchAgentRuntime(
        model=model,
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-finalization-retry",
        before_succeed=finalize,
    )
    with pytest.raises(ResearchAgentRuntimeError) as failure:
        await _collect(first.execute_turn(request))
    assert failure.value.kind is RuntimeErrorKind.TEMPORARY
    assert failure.value.code == "runtime_workspace_snapshot_failed"
    record = await control.get(request.turn_run_id)
    assert record is not None and record.state is RuntimeControlState.RUNNING
    model_calls = model.model_call_count

    second = DeepAgentsResearchAgentRuntime(
        model=model,
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-finalization-retry",
        before_succeed=finalize,
    )
    events = await _collect(
        second.resume_turn(
            RuntimeResumeRequest(
                turn_run_id=request.turn_run_id,
                response=None,
                turn_request=request,
            )
        )
    )

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert calls == 2
    assert model.model_call_count == model_calls
    record = await control.get(request.turn_run_id)
    assert record is not None and record.state is RuntimeControlState.SUCCEEDED


async def test_permanent_workspace_validation_failure_never_succeeds_runtime() -> None:
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, _, _, _ = await _controlled_runtime_dependencies(request.turn_run_id)

    async def reject(turn_request: RuntimeTurnRequest) -> None:
        del turn_request
        raise ValueError("symlink")

    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=MemorySaver(),
        execution_control=control,
        runtime_owner_id="runtime-finalization-invalid",
        before_succeed=reject,
    )

    with pytest.raises(ResearchAgentRuntimeError) as failure:
        await _collect(runtime.execute_turn(request))

    assert failure.value.kind is RuntimeErrorKind.PERMANENT
    assert failure.value.code == "runtime_workspace_snapshot_invalid"
    record = await control.get(request.turn_run_id)
    assert record is not None and record.state is RuntimeControlState.FAILED


async def test_snapshot_survives_control_success_response_loss_without_reexecution() -> None:
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, _, _, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    saver = MemorySaver()
    model = ScriptedDeepAgentChatModel()
    snapshots = 0
    original_succeed = control.succeed
    lose_response = True

    async def finalize(turn_request: RuntimeTurnRequest) -> None:
        nonlocal snapshots
        assert turn_request == request
        snapshots += 1

    async def succeed_then_lose_response(permit, checkpoint_id):
        nonlocal lose_response
        result = await original_succeed(permit, checkpoint_id)
        if lose_response:
            lose_response = False
            raise RuntimeExecutionControlError(
                "runtime_control_response_lost",
                "Runtime 成功响应暂时丢失",
                temporary=True,
            )
        return result

    control.succeed = succeed_then_lose_response  # type: ignore[method-assign]
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-control-response-loss",
        before_succeed=finalize,
    )

    with pytest.raises(ResearchAgentRuntimeError):
        await _collect(runtime.execute_turn(request))
    calls_after_loss = model.model_call_count
    record = await control.get(request.turn_run_id)
    assert record is not None and record.state is RuntimeControlState.SUCCEEDED

    events = await _collect(runtime.execute_turn(request))

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert snapshots == 1
    assert model.model_call_count == calls_after_loss


async def test_new_adapter_resumes_running_checkpoint_without_readding_human_message() -> None:
    """进程重建后 response=None 沿 checkpoint 继续，而不是再次提交用户输入。"""
    checkpointer = MemorySaver()
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    first_model = ScriptedDeepAgentChatModel()
    first = DeepAgentsResearchAgentRuntime(model=first_model, checkpointer=checkpointer)
    stream = first.execute_turn(request)
    assert (await anext(stream)).kind is RuntimeEventKind.BOUND
    assert (await anext(stream)).kind is RuntimeEventKind.STARTED
    await stream.aclose()
    assert first_model.model_call_count == 0

    second_model = ScriptedDeepAgentChatModel()
    second = DeepAgentsResearchAgentRuntime(model=second_model, checkpointer=checkpointer)
    events = await _collect(
        second.resume_turn(
            RuntimeResumeRequest(
                turn_run_id=request.turn_run_id,
                response=None,
                turn_request=request,
            )
        )
    )
    recovered = await second.reconcile_turn(request.turn_run_id)
    latest = await checkpointer.aget_tuple(
        {
            "configurable": {
                "thread_id": recovered.session_binding.runtime_thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": recovered.turn_binding.runtime_checkpoint_id,
            }
        }
    )
    assert latest is not None
    state = await second._graph.aget_state(latest.config)  # noqa: SLF001

    assert events[0].kind is RuntimeEventKind.RESUMED
    assert recovered.state is RuntimeExecutionState.SUCCEEDED
    assert second_model.model_call_count == 1
    texts = [message.text for message in state.values["messages"]]
    assert sum("第二轮继续" in text for text in texts) == 1


async def test_persistent_execution_fences_old_owner_and_new_owner_resumes() -> None:
    """旧 owner 失权后不能进入模型边界，新 owner 沿同一 Checkpoint 完成。"""
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, clock, attempts, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    checkpointer = MemorySaver()
    old_model = ScriptedDeepAgentChatModel()
    old_runtime = DeepAgentsResearchAgentRuntime(
        model=old_model,
        checkpointer=checkpointer,
        execution_control=control,
        runtime_owner_id="runtime-old",
        lease_heartbeat_interval_seconds=3600,
    )
    old_stream = old_runtime.execute_turn(request)
    assert (await anext(old_stream)).kind is RuntimeEventKind.BOUND
    assert (await anext(old_stream)).kind is RuntimeEventKind.STARTED
    first_record = await control.get(request.turn_run_id)
    assert first_record is not None and first_record.last_checkpoint_id

    await attempts.finish_if_running(
        "attempt-1", AttemptStatus.FAILED, clock[0] + timedelta(seconds=31)
    )
    await attempts.add(
        RunAttempt(
            attempt_id="attempt-2",
            run_id=request.turn_run_id,
            attempt_number=2,
            worker_id="worker-2",
            status=AttemptStatus.RUNNING,
            started_at=clock[0] + timedelta(seconds=31),
            heartbeat_at=clock[0] + timedelta(seconds=31),
        )
    )
    clock[0] += timedelta(seconds=31)

    new_model = ScriptedDeepAgentChatModel()
    new_runtime = DeepAgentsResearchAgentRuntime(
        model=new_model,
        checkpointer=checkpointer,
        execution_control=control,
        runtime_owner_id="runtime-new",
        lease_heartbeat_interval_seconds=3600,
    )
    before = await new_runtime.reconcile_turn(request.turn_run_id)
    assert before.state is RuntimeExecutionState.RUNNING
    assert before.resume_available
    await _collect(
        new_runtime.resume_turn(
            RuntimeResumeRequest(
                turn_run_id=request.turn_run_id,
                response=None,
                turn_request=request,
            )
        )
    )
    final_record = await control.get(request.turn_run_id)
    assert final_record is not None
    assert final_record.state.value == "succeeded"
    assert final_record.fencing_token == 2
    assert new_model.model_call_count == 1

    with pytest.raises(ResearchAgentRuntimeError) as stale:
        await anext(old_stream)
    assert stale.value.code == "runtime_execution_lease_lost"
    assert old_model.model_call_count == 0
    await old_stream.aclose()


async def test_recovery_without_checkpoint_adds_the_first_human_message_once() -> None:
    """DB claim 后、首个 Checkpoint 前崩溃时可安全执行首次图输入。"""
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, clock, attempts, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    first = await control.claim(
        turn_run_id=request.turn_run_id,
        session_id=request.session_id,
        runtime_execution_id=_opaque_id("execution", request.turn_run_id),
        request_hash=_request_hash(request),
        owner_id="runtime-crashed-before-checkpoint",
    )
    assert first.last_checkpoint_id is None
    await attempts.finish_if_running(
        "attempt-1", AttemptStatus.FAILED, clock[0] + timedelta(seconds=31)
    )
    await attempts.add(
        RunAttempt(
            attempt_id="attempt-2",
            run_id=request.turn_run_id,
            attempt_number=2,
            worker_id="worker-2",
            status=AttemptStatus.RUNNING,
            started_at=clock[0] + timedelta(seconds=31),
            heartbeat_at=clock[0] + timedelta(seconds=31),
        )
    )
    clock[0] += timedelta(seconds=31)

    saver = MemorySaver()
    model = ScriptedDeepAgentChatModel()
    recovered = DeepAgentsResearchAgentRuntime(
        model=model,
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-recovered-before-checkpoint",
    )
    await _collect(
        recovered.resume_turn(
            RuntimeResumeRequest(
                turn_run_id=request.turn_run_id,
                response=None,
                turn_request=request,
            )
        )
    )
    reconciliation = await recovered.reconcile_turn(request.turn_run_id)
    latest = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": reconciliation.session_binding.runtime_thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": reconciliation.turn_binding.runtime_checkpoint_id,
            }
        }
    )
    assert latest is not None
    state = await recovered._graph.aget_state(latest.config)  # noqa: SLF001
    humans = [item for item in state.values["messages"] if isinstance(item, HumanMessage)]
    assert [item.id for item in humans].count(request.user_message_id) == 1
    assert model.model_call_count == 1


async def test_recovery_uses_synced_checkpoint_when_control_record_was_not_advanced() -> None:
    """同步 Checkpoint 后、控制记录推进前崩溃时不能重复追加 HumanMessage。"""
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, clock, attempts, executions = await _controlled_runtime_dependencies(
        request.turn_run_id
    )
    saver = MemorySaver()
    first = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-crashed-after-synced-checkpoint",
        lease_heartbeat_interval_seconds=3600,
    )
    stream = first.execute_turn(request)
    assert (await anext(stream)).kind is RuntimeEventKind.BOUND
    assert (await anext(stream)).kind is RuntimeEventKind.STARTED
    recorded = await executions.get(request.turn_run_id)
    assert recorded is not None and recorded.last_checkpoint_id is not None
    assert await executions.save(replace(recorded, last_checkpoint_id=None), expected=recorded)
    await stream.aclose()

    await attempts.finish_if_running(
        "attempt-1", AttemptStatus.FAILED, clock[0] + timedelta(seconds=31)
    )
    await attempts.add(
        RunAttempt(
            attempt_id="attempt-2",
            run_id=request.turn_run_id,
            attempt_number=2,
            worker_id="worker-2",
            status=AttemptStatus.RUNNING,
            started_at=clock[0] + timedelta(seconds=31),
            heartbeat_at=clock[0] + timedelta(seconds=31),
        )
    )
    clock[0] += timedelta(seconds=31)

    recovered = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-recovered-after-synced-checkpoint",
    )

    class _GraphInputProxy:
        def __init__(self, graph) -> None:
            self._graph = graph
            self.inputs: list[Any] = []

        def astream(self, input, *args, **kwargs):
            self.inputs.append(input)
            return self._graph.astream(input, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._graph, name)

    proxy = _GraphInputProxy(recovered._graph)  # noqa: SLF001
    recovered._graph = proxy  # type: ignore[assignment]  # noqa: SLF001
    await _collect(
        recovered.resume_turn(
            RuntimeResumeRequest(
                turn_run_id=request.turn_run_id,
                response=None,
                turn_request=request,
            )
        )
    )
    reconciliation = await recovered.reconcile_turn(request.turn_run_id)
    latest = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": reconciliation.session_binding.runtime_thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": reconciliation.turn_binding.runtime_checkpoint_id,
            }
        }
    )
    assert latest is not None
    state = await recovered._graph.aget_state(latest.config)  # noqa: SLF001
    humans = [item for item in state.values["messages"] if isinstance(item, HumanMessage)]
    assert [item.id for item in humans].count(request.user_message_id) == 1
    assert proxy.inputs == [None]


async def test_recovery_prefers_newer_synced_checkpoint_over_stale_control_watermark() -> None:
    """控制水位停在 C1 时必须从物理最新 C2 恢复，不重放 C2 已确认模型。"""
    request = _request()
    control, clock, attempts, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    saver = MemorySaver()
    tool_calls: list[str] = []

    @tool
    def record_research_step(note: str) -> str:
        """记录一次确定性的研究步骤。"""
        tool_calls.append(note)
        return "recorded"

    first_model = ScriptedDeepAgentChatModel()
    first = DeepAgentsResearchAgentRuntime(
        model=first_model,
        tools=(record_research_step,),
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-crashed-with-stale-watermark",
        lease_heartbeat_interval_seconds=3600,
    )
    first_stream = first.execute_turn(request)
    assert (await anext(first_stream)).kind is RuntimeEventKind.BOUND
    assert (await anext(first_stream)).kind is RuntimeEventKind.STARTED
    recorded = await control.get(request.turn_run_id)
    assert recorded is not None and recorded.last_checkpoint_id is not None
    c1 = recorded.last_checkpoint_id
    await first_stream.aclose()

    physical_stream = first._graph.astream(  # noqa: SLF001
        None,
        first._config(  # noqa: SLF001
            request,
            checkpoint_id=c1,
            permit=recorded.permit,
        ),
        context=_TurnContext(
            turn_run_id=request.turn_run_id,
            allowed_tool_names=frozenset(request.policy_snapshot.allowed_tool_names),
            max_model_calls=request.policy_snapshot.max_model_calls,
            max_tool_calls=request.policy_snapshot.max_tool_calls,
            runtime_permit=recorded.permit,
        ),
        stream_mode="updates",
        durability="sync",
    )
    while first_model.model_call_count == 0:
        await anext(physical_stream)
    await physical_stream.aclose()
    physical_latest = await first._find_checkpoint(  # noqa: SLF001
        request.turn_run_id,
        fencing_token=recorded.fencing_token,
    )
    assert physical_latest is not None
    c2 = _checkpoint_id(physical_latest.config)
    assert c2 != c1
    assert first_model.model_call_count == 1
    assert tool_calls == []
    assert (await control.get(request.turn_run_id)).last_checkpoint_id == c1  # type: ignore[union-attr]

    await attempts.finish_if_running(
        "attempt-1", AttemptStatus.FAILED, clock[0] + timedelta(seconds=31)
    )
    await attempts.add(
        RunAttempt(
            attempt_id="attempt-2",
            run_id=request.turn_run_id,
            attempt_number=2,
            worker_id="worker-2",
            status=AttemptStatus.RUNNING,
            started_at=clock[0] + timedelta(seconds=31),
            heartbeat_at=clock[0] + timedelta(seconds=31),
        )
    )
    clock[0] += timedelta(seconds=31)

    recovered_model = ScriptedDeepAgentChatModel()
    recovered = DeepAgentsResearchAgentRuntime(
        model=recovered_model,
        tools=(record_research_step,),
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-recovered-from-newest-checkpoint",
    )

    class _GraphResumeProxy:
        def __init__(self, graph) -> None:
            self._graph = graph
            self.inputs: list[Any] = []
            self.checkpoint_ids: list[str | None] = []

        def astream(self, input, config, *args, **kwargs):
            self.inputs.append(input)
            self.checkpoint_ids.append(config.get("configurable", {}).get("checkpoint_id"))
            return self._graph.astream(input, config, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._graph, name)

    proxy = _GraphResumeProxy(recovered._graph)  # noqa: SLF001
    recovered._graph = proxy  # type: ignore[assignment]  # noqa: SLF001
    await _collect(
        recovered.resume_turn(
            RuntimeResumeRequest(
                turn_run_id=request.turn_run_id,
                response=None,
                turn_request=request,
            )
        )
    )

    assert proxy.inputs == [None]
    assert proxy.checkpoint_ids == [c2]
    assert recovered_model.model_call_count == 1
    assert tool_calls == ["第一轮受控记录"]


async def test_record_checkpoint_lookup_rejects_mismatched_stable_identity() -> None:
    """即使 checkpoint_id 命中，也不能接受与控制记录 Session 不同的状态。"""
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, _, _, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    saver = MemorySaver()
    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-checkpoint-identity",
        lease_heartbeat_interval_seconds=3600,
    )
    stream = runtime.execute_turn(request)
    assert (await anext(stream)).kind is RuntimeEventKind.BOUND
    assert (await anext(stream)).kind is RuntimeEventKind.STARTED
    record = await control.get(request.turn_run_id)
    assert record is not None and record.last_checkpoint_id is not None
    assert record.graph_revision == "deep-agent-graph.v6"
    checkpoint = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": _opaque_id("thread", request.session_id),
                "checkpoint_ns": "",
                "checkpoint_id": record.last_checkpoint_id,
            }
        }
    )
    assert checkpoint is not None
    assert checkpoint.metadata["agent_graph_revision"] == "deep-agent-graph.v6"
    tampered = checkpoint._replace(
        metadata={**checkpoint.metadata, "agent_runtime_session_id": "session-other"}
    )

    class _TamperedCheckpointLookup:
        async def aget_tuple(self, config):
            del config
            return tampered

    runtime._checkpointer = _TamperedCheckpointLookup()  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await runtime._checkpoint_for_record(record)  # noqa: SLF001

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_checkpoint_identity_mismatch"
    await stream.aclose()


async def test_record_checkpoint_lookup_rejects_v2_graph_revision() -> None:
    """统一 Tool 预算 State 后不能把旧 v2 Checkpoint 当作兼容状态恢复。"""
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, _, _, _ = await _controlled_runtime_dependencies(request.turn_run_id)
    saver = MemorySaver()
    runtime = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-old-checkpoint-revision",
    )
    await _collect(runtime.execute_turn(request))
    record = await control.get(request.turn_run_id)
    assert record is not None and record.last_checkpoint_id is not None
    assert record.graph_revision == "deep-agent-graph.v6"
    checkpoint = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": _opaque_id("thread", request.session_id),
                "checkpoint_ns": "",
                "checkpoint_id": record.last_checkpoint_id,
            }
        }
    )
    assert checkpoint is not None
    assert checkpoint.metadata["agent_graph_revision"] == "deep-agent-graph.v6"
    old_checkpoint = checkpoint._replace(
        metadata={**checkpoint.metadata, "agent_graph_revision": "deep-agent-graph.v2"}
    )

    class _OldCheckpointLookup:
        async def aget_tuple(self, config):
            del config
            return old_checkpoint

    runtime._checkpointer = _OldCheckpointLookup()  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await runtime._checkpoint_for_record(record)  # noqa: SLF001

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_version_incompatible"


async def test_persistent_failed_and_cancelled_states_survive_adapter_recreation() -> None:
    """FAILED/CANCELLED 不依赖旧 Adapter 的 `_local_turns`。"""
    failed_request = _request()
    failed_control, _, _, _ = await _controlled_runtime_dependencies(failed_request.turn_run_id)
    failed_saver = MemorySaver()
    failing = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=failed_saver,
        execution_control=failed_control,
        runtime_owner_id="runtime-failed",
        lease_heartbeat_interval_seconds=3600,
    )
    with pytest.raises(ResearchAgentRuntimeError) as failure:
        await _collect(failing.execute_turn(_request(allowed_tool_names=())))
    assert failure.value.code == "runtime_tool_not_allowed"
    recreated_failed = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=failed_saver,
        execution_control=failed_control,
        runtime_owner_id="runtime-recreated",
    )
    assert (
        await recreated_failed.reconcile_turn(failed_request.turn_run_id)
    ).state is RuntimeExecutionState.FAILED

    cancelled_request = _request(turn_run_id="turn-2", allowed_tool_names=())
    cancelled_control, _, _, _ = await _controlled_runtime_dependencies(
        cancelled_request.turn_run_id
    )
    cancelled_saver = MemorySaver()
    active = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=cancelled_saver,
        execution_control=cancelled_control,
        runtime_owner_id="runtime-cancelled",
        lease_heartbeat_interval_seconds=3600,
    )
    active_stream = active.execute_turn(cancelled_request)
    await anext(active_stream)
    await anext(active_stream)
    run_repo = cancelled_control._run_repo_factory(None)  # type: ignore[arg-type]  # noqa: SLF001
    run = await run_repo.get_by_id(cancelled_request.turn_run_id)
    assert run is not None
    assert await run_repo.update_status(
        run.run_id,
        RunStatus.RUNNING,
        RunStatus.CANCEL_REQUESTED,
        run.event_sequence + 1,
    )
    cancelled = await active.cancel_turn(cancelled_request.turn_run_id)
    await active_stream.aclose()
    assert cancelled.state is RuntimeExecutionState.CANCELLED
    recreated_cancelled = DeepAgentsResearchAgentRuntime(
        model=ScriptedDeepAgentChatModel(),
        checkpointer=cancelled_saver,
        execution_control=cancelled_control,
        runtime_owner_id="runtime-after-cancel",
    )
    assert (
        await recreated_cancelled.reconcile_turn(cancelled_request.turn_run_id)
    ).state is RuntimeExecutionState.CANCELLED


async def test_v2_runtime_graph_revision_is_permanent_and_does_not_call_model() -> None:
    request = _request(turn_run_id="turn-2", allowed_tool_names=())
    control, _, _, repo = await _controlled_runtime_dependencies(request.turn_run_id)
    saver = MemorySaver()
    first_model = ScriptedDeepAgentChatModel()
    first = DeepAgentsResearchAgentRuntime(
        model=first_model,
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-first",
    )
    await _collect(first.execute_turn(request))
    record = repo._items[request.turn_run_id]  # noqa: SLF001
    assert record.graph_revision == "deep-agent-graph.v6"
    repo._items[request.turn_run_id] = replace(  # noqa: SLF001
        record, graph_revision="deep-agent-graph.v2"
    )

    new_model = ScriptedDeepAgentChatModel()
    recreated = DeepAgentsResearchAgentRuntime(
        model=new_model,
        checkpointer=saver,
        execution_control=control,
        runtime_owner_id="runtime-new",
    )
    with pytest.raises(ResearchAgentRuntimeError) as incompatible:
        await recreated.reconcile_turn(request.turn_run_id)
    assert incompatible.value.code == "runtime_version_incompatible"
    assert new_model.model_call_count == 0


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
    await _collect(runtime.execute_turn(_request(turn_run_id="turn-2", max_model_calls=1)))
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
    assert latest["agent_model_budget_turn_run_id"] == "turn-2"
    assert latest["agent_model_budget_reserved_calls"] == 1
    assert "agent_turn_model_call_counts" not in latest
    assert {key for key in latest if key.startswith("agent_model_budget_")} == {
        "agent_model_budget_turn_run_id",
        "agent_model_budget_reserved_calls",
    }
    history_files = [path for path in latest["files"] if path.startswith("/conversation_history/")]
    assert len(history_files) == 1
    assert any(
        "已压缩：第一轮完成了受控研究记录" in item for item in model.observed_message_text[-1]
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
        await _collect(runtime.execute_turn(_request(allowed_tool_names=(), max_tool_calls=0)))

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_tool_not_allowed"
    assert tool_calls == []
    assert all(not names for names in model.visible_tool_names)
    assert (await runtime.reconcile_turn("turn-1")).state is RuntimeExecutionState.FAILED


async def test_project_tool_injects_turn_scope_and_hides_platform_ids_from_model() -> None:
    model = _ProjectToolModel()
    project_context = _ProjectContext()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        project_context=project_context,
        checkpointer=MemorySaver(),
    )
    request = _request(
        allowed_tool_names=("search_project_chunks",),
        max_tool_calls=1,
    )

    events = await _collect(runtime.execute_turn(request))
    result = await runtime.collect_turn_result(request.turn_run_id)

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert project_context.search_calls == [(request.turn_run_id, "图神经网络")]
    assert project_context.matrix_calls == []
    assert result.assistant_content == (
        "## 研究结论\n"
        "- 该方法得到本地论文支持[evidence:evidence-agent-1]\n"
        "仍需进一步验证。"
    )
    assert result.evidence_ids == ("evidence-agent-1",)
    search_schemas = [
        schemas["search_project_chunks"]
        for schemas in model.visible_tool_schemas
        if "search_project_chunks" in schemas
    ]
    assert search_schemas
    assert set(search_schemas[-1]["properties"]) == {"query"}
    assert all(
        forbidden not in str(search_schemas[-1])
        for forbidden in (
            "owner_id",
            "project_id",
            "snapshot_id",
            "review_output_id",
            "chunk_set_id",
            "turn_run_id",
        )
    )


async def test_previous_turn_project_tool_does_not_reclassify_natural_reply() -> None:
    model = _ProjectFirstThenNaturalReplyModel()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        project_context=_ProjectContext(),
        checkpointer=MemorySaver(),
    )

    await _collect(
        runtime.execute_turn(
            _request(allowed_tool_names=("search_project_chunks",), max_tool_calls=1)
        )
    )
    await _collect(
        runtime.execute_turn(
            _request(turn_run_id="turn-2", allowed_tool_names=(), max_tool_calls=0)
        )
    )
    result = await runtime.collect_turn_result("turn-2")

    assert result.assistant_content == "请明确要保存的内容，我会生成 TXT 文件。"
    assert result.evidence_ids == ()


async def test_matrix_tool_injects_turn_scope_with_no_model_controlled_arguments() -> None:
    model = _MatrixToolModel()
    project_context = _ProjectContext()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        project_context=project_context,
        checkpointer=MemorySaver(),
    )
    request = _request(
        allowed_tool_names=("read_review_evidence_matrix",),
        max_tool_calls=1,
    )

    events = await _collect(runtime.execute_turn(request))
    result = await runtime.collect_turn_result(request.turn_run_id)

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert project_context.matrix_calls == [request.turn_run_id]
    assert project_context.search_calls == []
    assert result.assistant_content == "当前授权上下文证据不足。"
    matrix_schemas = [
        schemas["read_review_evidence_matrix"]
        for schemas in model.visible_tool_schemas
        if "read_review_evidence_matrix" in schemas
    ]
    assert matrix_schemas
    assert matrix_schemas[-1].get("properties", {}) == {}


async def test_project_context_temporary_error_maps_to_runtime_temporary_error() -> None:
    model = _MatrixToolModel()
    project_context = _FailingMatrixContext()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        project_context=project_context,
        checkpointer=MemorySaver(),
    )
    request = _request(
        allowed_tool_names=("read_review_evidence_matrix",),
        max_tool_calls=1,
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(runtime.execute_turn(request))

    assert project_context.matrix_calls == [request.turn_run_id]
    assert exc_info.value.kind is RuntimeErrorKind.TEMPORARY
    assert exc_info.value.code == "project_context_matrix_unavailable"
    assert exc_info.value.safe_message == "Matrix 暂时不可用"


async def test_unapproved_project_tool_is_hidden_and_rejected_before_port_call() -> None:
    model = _ProjectToolModel()
    project_context = _ProjectContext()
    runtime = DeepAgentsResearchAgentRuntime(
        model=model,
        project_context=project_context,
        checkpointer=MemorySaver(),
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await _collect(runtime.execute_turn(_request(allowed_tool_names=(), max_tool_calls=0)))

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_tool_not_allowed"
    assert project_context.search_calls == []
    assert all("search_project_chunks" not in names for names in model.visible_tool_names)


async def test_policy_allows_only_the_selected_filesystem_tool() -> None:
    model = ScriptedDeepAgentChatModel()
    runtime = DeepAgentsResearchAgentRuntime(model=model, checkpointer=MemorySaver())

    result = await _collect(
        runtime.execute_turn(_request(turn_run_id="turn-2", allowed_tool_names=("read_file",)))
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
            runtime.execute_turn(_request(allowed_tool_names=("execute",), max_tool_calls=1))
        )

    assert exc_info.value.code == "runtime_tool_not_allowed"
    assert calls == []
    assert all("execute" not in names for names in model.visible_tool_names)


def test_restricted_harness_does_not_globally_exclude_sandbox_execute(monkeypatch) -> None:
    captured: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        runtime_module,
        "register_harness_profile",
        lambda key, profile: captured.append((key, profile)),
    )

    DeepAgentsResearchAgentRuntime._register_restricted_harness_profile(  # noqa: SLF001
        ScriptedDeepAgentChatModel(model_name="sandbox-execute-model"),
        allow_execute=True,
    )

    assert len(captured) == 1
    key, profile = captured[0]
    assert key == "phase5fake:sandbox-execute-model"
    assert profile.excluded_tools == frozenset()
    assert profile.general_purpose_subagent.enabled is False


def test_restricted_harness_excludes_execute_without_executable_backend(monkeypatch) -> None:
    captured: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        runtime_module,
        "register_harness_profile",
        lambda key, profile: captured.append((key, profile)),
    )

    DeepAgentsResearchAgentRuntime._register_restricted_harness_profile(  # noqa: SLF001
        ScriptedDeepAgentChatModel(model_name="state-backend-model"),
        allow_execute=False,
    )

    assert len(captured) == 1
    assert captured[0][1].excluded_tools == frozenset({"execute"})


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
