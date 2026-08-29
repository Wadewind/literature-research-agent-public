import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from deepagents.backends import StateBackend
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeTurnRequest,
)
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
)
from literature_agent.domain.mcp_configuration import (
    McpPolicyRef,
    McpPolicyToolRef,
    canonical_json_hash,
)
from literature_agent.domain.research_agent import (
    create_context_snapshot,
    create_project_research_workspace_policy_snapshot,
)
from literature_agent.infrastructure.agent import mcp_tools as mcp_tools_module
from literature_agent.infrastructure.agent.mcp_tools import (
    LangchainMcpToolLoader,
    PlatformMcpToolInterceptor,
    ResolvedMcpConnection,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseRecord,
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
)


def _server(calls: list[str] | None = None) -> FastMCP:
    server = FastMCP("fixture-search")

    @server.tool()
    def search(query: str) -> dict[str, str]:
        """确定性检索。"""
        if calls is not None:
            calls.append(query)
        return {"answer": f"fixture:{query}"}

    return server


class _Resolver:
    async def resolve(self, request, ref, lease):
        assert request.turn_run_id == "turn-1"
        assert lease.record.session_id == "session-1"
        assert ref.config_hash == "a" * 64
        return ResolvedMcpConnection(ref.catalog_id, {"transport": "memory"})


class _Sessions:
    def __init__(self, server: FastMCP) -> None:
        self.server = server
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def open(self, server_name, connection):
        assert server_name == "fixture-search"
        assert connection == {"transport": "memory"}
        self.opened += 1
        try:
            async with create_connected_server_and_client_session(self.server) as session:
                try:
                    yield session
                finally:
                    self.closed += 1
        except BaseExceptionGroup as group:
            raise _first_leaf(group) from None


class _Guard:
    def __init__(self) -> None:
        self.active_checks = 0
        self.results: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.failures: list[str] = []

    async def assert_active(self, turn_run_id):
        assert turn_run_id == "turn-1"
        self.active_checks += 1

    async def begin(self, turn_run_id, tool_name, arguments, *, invocation_id):
        return self.results.get(
            (turn_run_id, invocation_id, tool_name, str(sorted(arguments.items())))
        )

    async def succeed(self, turn_run_id, tool_name, arguments, result_payload, *, invocation_id):
        self.results[(turn_run_id, invocation_id, tool_name, str(sorted(arguments.items())))] = (
            result_payload
        )

    async def fail(
        self,
        turn_run_id,
        tool_name,
        arguments,
        *,
        invocation_id,
        kind,
        code,
        safe_message,
    ):
        self.failures.append(code)


async def _tool_ref(server: FastMCP) -> McpPolicyToolRef:
    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
    tool = listed.tools[0]
    return McpPolicyToolRef(
        name=f"fixture-search_{tool.name}",
        input_schema_hash=canonical_json_hash(tool.inputSchema),
    )


async def _request(server: FastMCP, *, schema_hash: str | None = None) -> RuntimeTurnRequest:
    tool_ref = await _tool_ref(server)
    if schema_hash is not None:
        tool_ref = McpPolicyToolRef(tool_ref.name, schema_hash)
    ref = McpPolicyRef(
        profile_id="profile-1",
        profile_revision=1,
        catalog_id="fixture-search",
        version="1.0.0",
        config_hash="a" * 64,
        tools=(tool_ref,),
    )
    context = create_context_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        user_message_id="message-1",
        history_through_sequence=1,
        review_output_id="output-1",
    )
    policy = create_project_research_workspace_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        mcp_refs=(ref,),
    )
    return RuntimeTurnRequest(
        session_id="session-1",
        turn_run_id="turn-1",
        user_message_id="message-1",
        user_message_content="search",
        context_snapshot=context,
        policy_snapshot=policy,
    )


def _lease() -> SandboxWorkspaceLease:
    now = datetime.now(UTC)
    return SandboxWorkspaceLease(
        record=SandboxLeaseRecord(
            session_id="session-1",
            owner_id="owner-1",
            project_id="project-1",
            holder_turn_run_id="turn-1",
            sandbox_id="sandbox-1",
            image_ref="image@sha256:test",
            generation=1,
            fencing_token=1,
            status=SandboxLeaseStatus.ACTIVE,
            generation_started_at=now,
            expires_at=now + timedelta(minutes=10),
            updated_at=now,
        ),
        backend=StateBackend(),
    )


@pytest.mark.asyncio
async def test_loader_uses_prefixed_tools_explicit_session_and_replays_effect() -> None:
    calls: list[str] = []
    server = _server(calls)
    sessions = _Sessions(server)
    guard = _Guard()
    loader = LangchainMcpToolLoader(
        connection_resolver=_Resolver(),
        guard=guard,
        session_factory=sessions,
    )

    async with loader.open(await _request(server), _lease()) as tools:
        assert [tool.name for tool in tools] == ["fixture-search_search"]
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode(list(tools)))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()

        async def invoke(call_id: str):
            value = await cast(Any, graph).ainvoke(
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "name": "fixture-search_search",
                                    "args": {"query": "rag"},
                                    "id": call_id,
                                    "type": "tool_call",
                                }
                            ],
                        }
                    ]
                }
            )
            return value["messages"][-1].content

        first = await invoke("call-1")
        replay = await invoke("call-1")
        second_invocation = await invoke("call-2")
        assert "fixture:rag" in str(first)
        assert replay[0]["text"] == first[0]["text"]
        assert second_invocation[0]["text"] == first[0]["text"]
        assert calls == ["rag", "rag"]
        assert sessions.closed == 0

    assert sessions.opened == 1
    assert sessions.closed == 1
    assert guard.active_checks >= 4
    assert guard.failures == []


@pytest.mark.asyncio
async def test_loader_fails_closed_on_schema_drift_and_closes_session() -> None:
    server = _server()
    sessions = _Sessions(server)
    loader = LangchainMcpToolLoader(
        connection_resolver=_Resolver(),
        guard=_Guard(),
        session_factory=sessions,
    )

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        async with loader.open(await _request(server, schema_hash="0" * 64), _lease()):
            pass

    assert caught.value.code == "runtime_mcp_schema_drift"
    assert sessions.opened == sessions.closed == 1


@pytest.mark.asyncio
async def test_loader_projects_only_catalog_tools_from_all_discovery_pages() -> None:
    """Server 可提供更多 Tool，但模型只能看到完整分页后命中的冻结 allowlist。"""
    server = _server()
    async with create_connected_server_and_client_session(server) as session:
        allowed = (await session.list_tools()).tools[0]
    extra = Tool(
        name="download_paper",
        description="未授权的写文件能力",
        inputSchema={
            "type": "object",
            "properties": {"paper_id": {"type": "string"}},
            "required": ["paper_id"],
        },
    )

    class _PagedSession:
        def __init__(self) -> None:
            self.cursors: list[str | None] = []

        async def list_tools(self, cursor: str | None = None):
            self.cursors.append(cursor)
            if cursor is None:
                return ListToolsResult(tools=[extra], nextCursor="allowed-page")
            assert cursor == "allowed-page"
            return ListToolsResult(tools=[allowed])

    paged = _PagedSession()

    class _PagedSessions:
        @asynccontextmanager
        async def open(self, server_name, connection):
            del server_name, connection
            yield paged

    loader = LangchainMcpToolLoader(
        connection_resolver=_Resolver(),
        guard=_Guard(),
        session_factory=cast(Any, _PagedSessions()),
    )

    async with loader.open(await _request(server), _lease()) as tools:
        assert [tool.name for tool in tools] == ["fixture-search_search"]

    assert paged.cursors == [None, "allowed-page"]


@pytest.mark.asyncio
async def test_loader_checks_cancellation_before_resolving_or_connecting() -> None:
    server = _server()
    sessions = _Sessions(server)

    class _UnusedResolver:
        calls = 0

        async def resolve(self, request, ref, lease):
            self.calls += 1
            pytest.fail("取消后不得解析 MCP 连接")

    class _CancelledGuard(_Guard):
        async def assert_active(self, turn_run_id):
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.CANCELLED,
                code="runtime_mcp_cancelled",
                safe_message="Agent Turn 已请求取消",
            )

    resolver = _UnusedResolver()
    loader = LangchainMcpToolLoader(
        connection_resolver=resolver,
        guard=_CancelledGuard(),
        session_factory=sessions,
    )

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        async with loader.open(await _request(server), _lease()):
            pass

    assert caught.value.code == "runtime_mcp_cancelled"
    assert resolver.calls == 0
    assert sessions.opened == sessions.closed == 0


@pytest.mark.asyncio
async def test_loader_redacts_resolver_connection_error() -> None:
    server = _server()

    class _SecretResolver:
        async def resolve(self, request, ref, lease):
            raise RuntimeError("https://secret-mcp.example?token=must-not-leak")

    loader = LangchainMcpToolLoader(
        connection_resolver=_SecretResolver(),
        guard=_Guard(),
        session_factory=_Sessions(server),
    )

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        async with loader.open(await _request(server), _lease()):
            pass

    assert caught.value.code == "runtime_mcp_unavailable"
    assert caught.value.kind is RuntimeErrorKind.TEMPORARY
    assert caught.value.safe_message == "MCP 能力暂时不可用"
    assert caught.value.__cause__ is None
    assert "secret-mcp" not in str(caught.value)
    assert "must-not-leak" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["session", "list", "load", "close"])
async def test_loader_redacts_every_sdk_lifecycle_boundary(
    failure_stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server()
    secret_error = RuntimeError(f"{failure_stage}:https://secret-mcp.example?token=must-not-leak")

    class _LifecycleSessions(_Sessions):
        def open(self, server_name, connection):
            if failure_stage == "session":
                raise secret_error

            @asynccontextmanager
            async def context():
                async with create_connected_server_and_client_session(server) as session:
                    if failure_stage == "list":

                        async def fail_list(cursor: str | None = None) -> ListToolsResult:
                            del cursor
                            raise secret_error

                        cast(Any, session).list_tools = fail_list
                    yield session
                if failure_stage == "close":
                    raise secret_error

            return context()

    if failure_stage == "load":

        def fail_load(*args, **kwargs):
            raise secret_error

        monkeypatch.setattr(mcp_tools_module, "convert_mcp_tool_to_langchain_tool", fail_load)

    loader = LangchainMcpToolLoader(
        connection_resolver=_Resolver(),
        guard=_Guard(),
        session_factory=_LifecycleSessions(server),
    )

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        async with loader.open(await _request(server), _lease()):
            pass

    assert caught.value.code == "runtime_mcp_unavailable"
    assert caught.value.kind is RuntimeErrorKind.TEMPORARY
    assert caught.value.safe_message == "MCP 能力暂时不可用"
    assert caught.value.__cause__ is None
    assert "secret-mcp" not in str(caught.value)
    assert "must-not-leak" not in str(caught.value)


@pytest.mark.asyncio
async def test_interceptor_rejects_missing_invocation_id_before_mcp() -> None:
    server = _server()
    request = await _request(server)
    guard = _Guard()
    interceptor = PlatformMcpToolInterceptor(
        turn_run_id="turn-1", ref=request.policy_snapshot.mcp_refs[0], guard=guard
    )
    called = False

    async def handler(_):
        nonlocal called
        called = True

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await interceptor(
            MCPToolCallRequest(
                name="search",
                args={"query": "rag"},
                server_name="fixture-search",
                runtime=SimpleNamespace(context=None),
            ),
            handler,
        )

    assert caught.value.code == "runtime_mcp_invocation_missing"
    assert called is False
    assert guard.active_checks == 0
    assert guard.failures == []


@pytest.mark.asyncio
async def test_interceptor_blocks_cancel_and_budget_before_calling_mcp() -> None:
    server = _server()
    request = await _request(server)
    ref = request.policy_snapshot.mcp_refs[0]

    class _BlockingGuard(_Guard):
        def __init__(self, code: str) -> None:
            super().__init__()
            self.code = code

        async def assert_active(self, turn_run_id):
            if self.code == "runtime_mcp_cancelled":
                raise ResearchAgentRuntimeError(
                    kind=RuntimeErrorKind.CANCELLED,
                    code=self.code,
                    safe_message="Agent Turn 已请求取消",
                )

        async def begin(self, turn_run_id, tool_name, arguments, *, invocation_id):
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.PERMANENT,
                code=self.code,
                safe_message="本轮 Tool 调用预算已耗尽",
            )

    async def handler(_):
        pytest.fail("取消或预算耗尽后不得发起 MCP 调用")

    raw_request = MCPToolCallRequest(
        name="search",
        args={"query": "rag"},
        server_name="fixture-search",
        runtime=SimpleNamespace(tool_call_id="call-blocked", context=None),
    )
    for code in ("runtime_mcp_cancelled", "runtime_mcp_tool_budget_exceeded"):
        interceptor = PlatformMcpToolInterceptor(
            turn_run_id="turn-1", ref=ref, guard=_BlockingGuard(code)
        )
        with pytest.raises(ResearchAgentRuntimeError) as caught:
            await interceptor(raw_request, handler)
        assert caught.value.code == code


@pytest.mark.asyncio
async def test_interceptor_bounds_oversized_text_output_without_failing_turn() -> None:
    server = _server()
    request = await _request(server)
    guard = _Guard()
    interceptor = PlatformMcpToolInterceptor(
        turn_run_id="turn-1", ref=request.policy_snapshot.mcp_refs[0], guard=guard
    )

    async def handler(_):
        return CallToolResult(content=[TextContent(type="text", text="x" * 9_000)])

    result = await interceptor(
        MCPToolCallRequest(
            name="search",
            args={"query": "rag"},
            server_name="fixture-search",
            runtime=SimpleNamespace(tool_call_id="call-large", context=None),
        ),
        handler,
    )

    assert result.isError is False
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert "平台已截断" in result.content[0].text
    assert len(result.content[0].text) < 8_000
    assert guard.failures == []
    assert len(guard.results) == 1


@pytest.mark.asyncio
async def test_interceptor_closes_effect_when_outer_runtime_cancels_call() -> None:
    server = _server()
    request = await _request(server)
    guard = _Guard()
    interceptor = PlatformMcpToolInterceptor(
        turn_run_id="turn-1", ref=request.policy_snapshot.mcp_refs[0], guard=guard
    )
    entered = asyncio.Event()

    async def handler(_):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        interceptor(
            MCPToolCallRequest(
                name="search",
                args={"query": "rag"},
                server_name="fixture-search",
                runtime=SimpleNamespace(tool_call_id="call-cancelled", context=None),
            ),
            handler,
        )
    )
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert guard.failures == ["runtime_mcp_interrupted"]


@pytest.mark.asyncio
async def test_interceptor_rechecks_runtime_fence_immediately_before_mcp_call() -> None:
    server = _server()
    request = await _request(server)

    class _Fence:
        calls = 0

        async def assert_active(self, permit):
            assert permit == "permit-1"
            self.calls += 1
            if self.calls == 2:
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost",
                    "Runtime Execution lease 已失效",
                )

    fence = _Fence()
    guard = _Guard()
    interceptor = PlatformMcpToolInterceptor(
        turn_run_id="turn-1",
        ref=request.policy_snapshot.mcp_refs[0],
        guard=guard,
        execution_control=cast(Any, fence),
    )
    called = False

    async def handler(_):
        nonlocal called
        called = True
        return CallToolResult(content=[TextContent(type="text", text="result")])

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await interceptor(
            MCPToolCallRequest(
                name="search",
                args={"query": "rag"},
                server_name="fixture-search",
                runtime=SimpleNamespace(
                    tool_call_id="call-fenced",
                    context=SimpleNamespace(turn_run_id="turn-1", runtime_permit="permit-1"),
                ),
            ),
            handler,
        )

    assert caught.value.code == "runtime_execution_lease_lost"
    assert called is False
    assert fence.calls == 2
    assert guard.failures == []


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_error", [TimeoutError(), RuntimeError("secret")])
async def test_interceptor_does_not_fail_effect_after_handler_loses_fence(
    handler_error: Exception,
) -> None:
    server = _server()
    request = await _request(server)

    class _Fence:
        calls = 0

        async def assert_active(self, permit):
            assert permit == "permit-1"
            self.calls += 1
            if self.calls == 3:
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost",
                    "Runtime Execution lease 已失效",
                )

    fence = _Fence()
    guard = _Guard()
    interceptor = PlatformMcpToolInterceptor(
        turn_run_id="turn-1",
        ref=request.policy_snapshot.mcp_refs[0],
        guard=guard,
        execution_control=cast(Any, fence),
    )
    called = False

    async def handler(_):
        nonlocal called
        called = True
        raise handler_error

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await interceptor(
            MCPToolCallRequest(
                name="search",
                args={"query": "rag"},
                server_name="fixture-search",
                runtime=SimpleNamespace(
                    tool_call_id="call-fence-lost-after-handler",
                    context=SimpleNamespace(turn_run_id="turn-1", runtime_permit="permit-1"),
                ),
            ),
            handler,
        )

    assert caught.value.code == "runtime_execution_lease_lost"
    assert called is True
    assert fence.calls == 3
    assert guard.failures == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_error", "expected_code"),
    [
        (TimeoutError(), "runtime_mcp_timeout"),
        (RuntimeError("secret"), "runtime_mcp_unavailable"),
    ],
)
async def test_interceptor_records_safe_failure_when_fence_remains_owned(
    handler_error: Exception,
    expected_code: str,
) -> None:
    server = _server()
    request = await _request(server)

    class _Fence:
        calls = 0

        async def assert_active(self, permit):
            assert permit == "permit-1"
            self.calls += 1

    fence = _Fence()
    guard = _Guard()
    interceptor = PlatformMcpToolInterceptor(
        turn_run_id="turn-1",
        ref=request.policy_snapshot.mcp_refs[0],
        guard=guard,
        execution_control=cast(Any, fence),
    )

    async def handler(_):
        raise handler_error

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await interceptor(
            MCPToolCallRequest(
                name="search",
                args={"query": "rag"},
                server_name="fixture-search",
                runtime=SimpleNamespace(
                    tool_call_id="call-fence-owned-after-handler",
                    context=SimpleNamespace(turn_run_id="turn-1", runtime_permit="permit-1"),
                ),
            ),
            handler,
        )

    assert caught.value.code == expected_code
    assert fence.calls == 3
    assert guard.failures == [expected_code]


def _first_leaf(group: BaseExceptionGroup) -> BaseException:
    value: BaseException = group
    while isinstance(value, BaseExceptionGroup):
        value = value.exceptions[0]
    return value
