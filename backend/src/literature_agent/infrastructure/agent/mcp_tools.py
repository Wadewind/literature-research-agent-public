"""显式 MCP ClientSession 生命周期、契约校验与调用拦截。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    ToolCallInterceptor,
)
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp import ClientSession
from mcp.types import CallToolResult, Tool

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeTurnRequest,
)
from literature_agent.application.ports.runtime_execution_control import (
    RuntimeExecutionControl,
)
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
)
from literature_agent.domain.mcp_configuration import (
    McpPolicyRef,
    canonical_json_hash,
)
from literature_agent.domain.tool_execution import TOOL_RESULT_MAX_CHARS, ToolErrorKind
from literature_agent.infrastructure.agent.sandbox_workspace import SandboxWorkspaceLease

_MAX_MCP_DISCOVERY_PAGES = 32
_MAX_MCP_DISCOVERED_TOOLS = 256


@dataclass(frozen=True, slots=True)
class ResolvedMcpConnection:
    """仅存在于 infrastructure 的连接配置。"""

    server_name: str
    connection: Mapping[str, Any] = field(repr=False)


class McpConnectionResolver(Protocol):
    """按冻结引用解析精确平台注册版本；实现必须复核 config hash。"""

    async def resolve(
        self,
        request: RuntimeTurnRequest,
        ref: McpPolicyRef,
        lease: SandboxWorkspaceLease,
    ) -> ResolvedMcpConnection: ...


class RejectingMcpConnectionResolver:
    """Catalog 尚无已安装 Server 时的生产 fail-closed 解析器。"""

    async def resolve(
        self,
        request: RuntimeTurnRequest,
        ref: McpPolicyRef,
        lease: SandboxWorkspaceLease,
    ) -> ResolvedMcpConnection:
        raise _error("runtime_mcp_catalog_unavailable", "MCP Catalog 条目未安装")


class McpInvocationGuard(Protocol):
    """SDK-neutral 调用账本；每个方法自行使用短事务。"""

    async def assert_active(self, turn_run_id: str) -> None: ...
    async def begin(
        self,
        turn_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
    ) -> dict[str, Any] | None: ...
    async def succeed(
        self,
        turn_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result_payload: dict[str, Any],
        *,
        invocation_id: str,
    ) -> None: ...
    async def fail(
        self,
        turn_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
        kind: ToolErrorKind,
        code: str,
        safe_message: str,
    ) -> None: ...


class McpClientSessionFactory(Protocol):
    def open(self, server_name: str, connection: Mapping[str, Any]) -> Any: ...


class LangchainMcpClientSessionFactory:
    """每个 Server/Runtime execution 创建一个显式、可关闭的 Session。"""

    @asynccontextmanager
    async def open(
        self, server_name: str, connection: Mapping[str, Any]
    ) -> AsyncIterator[ClientSession]:
        client = MultiServerMCPClient(
            {server_name: cast(Any, dict(connection))},
            tool_name_prefix=True,
        )
        async with client.session(server_name) as session:
            yield session


class PlatformMcpToolInterceptor:
    """在真实 MCP 调用处执行 scope、取消、幂等、超时和输出限制。"""

    def __init__(
        self,
        *,
        turn_run_id: str,
        ref: McpPolicyRef,
        guard: McpInvocationGuard,
        execution_control: RuntimeExecutionControl | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._turn_run_id = turn_run_id
        self._ref = ref
        self._guard = guard
        self._execution_control = execution_control
        self._timeout_seconds = timeout_seconds
        self._allowed_raw_names = {
            tool.name.removeprefix(f"{ref.catalog_id}_") for tool in ref.tools
        }

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if (
            request.server_name != self._ref.catalog_id
            or request.name not in self._allowed_raw_names
        ):
            raise _error("runtime_mcp_tool_not_allowed", "MCP Tool 未被本轮策略授权")
        runtime_context = getattr(request.runtime, "context", None)
        runtime_turn_id = getattr(runtime_context, "turn_run_id", self._turn_run_id)
        if runtime_turn_id != self._turn_run_id:
            raise _error("runtime_mcp_scope_mismatch", "MCP Tool Turn scope 不匹配")
        prefixed_name = f"{request.server_name}_{request.name}"
        invocation_id = getattr(request.runtime, "tool_call_id", None)
        if (
            not isinstance(invocation_id, str)
            or not invocation_id.strip()
            or len(invocation_id) > 255
        ):
            raise _error("runtime_mcp_invocation_missing", "MCP Tool 缺少逻辑调用 ID")
        await self._assert_execution_active(runtime_context)
        await self._guard.assert_active(self._turn_run_id)
        replay = await self._guard.begin(
            self._turn_run_id,
            prefixed_name,
            request.args,
            invocation_id=invocation_id,
        )
        if replay is not None:
            return CallToolResult.model_validate(replay)
        try:
            await self._assert_execution_active(runtime_context)
            await self._guard.assert_active(self._turn_run_id)
            async with asyncio.timeout(self._timeout_seconds):
                result = await handler(request)
            if not isinstance(result, CallToolResult):
                raise _error("runtime_mcp_result_invalid", "MCP Tool 返回类型非法")
            payload = cast(dict[str, Any], result.model_dump(mode="json", exclude_none=True))
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if len(serialized) > TOOL_RESULT_MAX_CHARS:
                raise _error("runtime_mcp_output_too_large", "MCP Tool 输出超过安全上限")
            await self._assert_execution_active(runtime_context)
            await self._guard.assert_active(self._turn_run_id)
            await self._guard.succeed(
                self._turn_run_id,
                prefixed_name,
                request.args,
                payload,
                invocation_id=invocation_id,
            )
            return result
        except TimeoutError:
            await self._fail_if_owned(
                runtime_context,
                prefixed_name,
                request.args,
                invocation_id=invocation_id,
                kind=ToolErrorKind.TEMPORARY,
                code="runtime_mcp_timeout",
                safe_message="MCP Tool 调用超时",
            )
            raise _error(
                "runtime_mcp_timeout",
                "MCP Tool 调用超时",
                RuntimeErrorKind.TEMPORARY,
            ) from None
        except ResearchAgentRuntimeError as exc:
            if exc.code in {
                "runtime_execution_lease_lost",
                "runtime_execution_lease_missing",
            }:
                # 旧 fence 不能再写 ToolExecution；保留 RUNNING 供对账，禁止盲目重放。
                raise
            await self._fail_if_owned(
                runtime_context,
                prefixed_name,
                request.args,
                invocation_id=invocation_id,
                kind=(
                    ToolErrorKind.CANCELLED
                    if exc.kind is RuntimeErrorKind.CANCELLED
                    else ToolErrorKind.TEMPORARY
                    if exc.kind is RuntimeErrorKind.TEMPORARY
                    else ToolErrorKind.PERMANENT
                ),
                code=exc.code,
                safe_message=exc.safe_message,
            )
            raise
        except Exception:
            await self._fail_if_owned(
                runtime_context,
                prefixed_name,
                request.args,
                invocation_id=invocation_id,
                kind=ToolErrorKind.TEMPORARY,
                code="runtime_mcp_unavailable",
                safe_message="MCP Tool 暂时不可用",
            )
            raise _error(
                "runtime_mcp_unavailable", "MCP Tool 暂时不可用", RuntimeErrorKind.TEMPORARY
            ) from None

    async def _fail_if_owned(
        self,
        runtime_context: object | None,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
        kind: ToolErrorKind,
        code: str,
        safe_message: str,
    ) -> None:
        await self._assert_execution_active(runtime_context)
        await self._guard.fail(
            self._turn_run_id,
            tool_name,
            arguments,
            invocation_id=invocation_id,
            kind=kind,
            code=code,
            safe_message=safe_message,
        )

    async def _assert_execution_active(self, runtime_context: object | None) -> None:
        if self._execution_control is None:
            return
        permit = getattr(runtime_context, "runtime_permit", None)
        if permit is None:
            raise _error("runtime_execution_lease_missing", "Runtime Execution 缺少 lease")
        try:
            await self._execution_control.assert_active(permit)
        except RuntimeExecutionControlError as exc:
            kind = (
                RuntimeErrorKind.CANCELLED
                if exc.code == "runtime_turn_cancelled"
                else RuntimeErrorKind.TEMPORARY
                if exc.temporary
                else RuntimeErrorKind.PERMANENT
            )
            raise _error(exc.code, exc.safe_message, kind) from exc


class LangchainMcpToolLoader:
    """加载经过命名空间、Schema/hash 与 interceptor 校验的 MCP Tools。"""

    def __init__(
        self,
        *,
        connection_resolver: McpConnectionResolver,
        guard: McpInvocationGuard,
        session_factory: McpClientSessionFactory | None = None,
        execution_control: RuntimeExecutionControl | None = None,
    ) -> None:
        self._resolver = connection_resolver
        self._guard = guard
        self._sessions = session_factory or LangchainMcpClientSessionFactory()
        self._execution_control = execution_control

    @asynccontextmanager
    async def open(
        self, request: RuntimeTurnRequest, lease: SandboxWorkspaceLease
    ) -> AsyncIterator[tuple[BaseTool, ...]]:
        refs = request.policy_snapshot.mcp_refs
        if not refs:
            yield ()
            return
        loaded: list[BaseTool] = []
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            for ref in refs:
                await self._guard.assert_active(request.turn_run_id)
                resolved = await _external_boundary(
                    lambda ref=ref: self._resolver.resolve(request, ref, lease)
                )
                if resolved.server_name != ref.catalog_id:
                    raise _error("runtime_mcp_catalog_mismatch", "MCP Catalog 解析结果不匹配")
                await self._guard.assert_active(request.turn_run_id)
                session = await _external_boundary(
                    lambda resolved=resolved: stack.enter_async_context(
                        self._sessions.open(resolved.server_name, resolved.connection)
                    )
                )
                await self._guard.assert_active(request.turn_run_id)
                definitions = await _list_all_tool_definitions(
                    session,
                    guard=self._guard,
                    turn_run_id=request.turn_run_id,
                )
                actual = {item.name: canonical_json_hash(item.inputSchema) for item in definitions}
                expected = {
                    tool.name.removeprefix(f"{ref.catalog_id}_"): tool.input_schema_hash
                    for tool in ref.tools
                }
                if any(actual.get(name) != schema_hash for name, schema_hash in expected.items()):
                    raise _error("runtime_mcp_schema_drift", "MCP Tool 名称或 Schema 已漂移")
                interceptor: ToolCallInterceptor = PlatformMcpToolInterceptor(
                    turn_run_id=request.turn_run_id,
                    ref=ref,
                    guard=self._guard,
                    execution_control=self._execution_control,
                )
                await self._guard.assert_active(request.turn_run_id)
                tools = _convert_allowed_tools(
                    session=session,
                    definitions=definitions,
                    allowed_raw_names=frozenset(expected),
                    server_name=ref.catalog_id,
                    interceptor=interceptor,
                )
                if {tool.name for tool in tools} != {tool.name for tool in ref.tools}:
                    raise _error("runtime_mcp_namespace_mismatch", "MCP Tool 命名空间不匹配")
                loaded.extend(tools)
            if len({tool.name for tool in loaded}) != len(loaded):
                raise _error("runtime_mcp_tool_name_conflict", "MCP Tool 名称冲突")
            yield tuple(loaded)
        finally:
            try:
                await stack.aclose()
            except asyncio.CancelledError:
                raise
            except ResearchAgentRuntimeError:
                raise
            except Exception:
                raise _error(
                    "runtime_mcp_unavailable",
                    "MCP 能力暂时不可用",
                    RuntimeErrorKind.TEMPORARY,
                ) from None


async def _list_all_tool_definitions(
    session: ClientSession,
    *,
    guard: McpInvocationGuard,
    turn_run_id: str,
) -> tuple[Tool, ...]:
    """遍历完整分页并拒绝重复、循环或无界的第三方能力清单。"""

    definitions: list[Tool] = []
    names: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: str | None = None
    for _ in range(_MAX_MCP_DISCOVERY_PAGES):
        await guard.assert_active(turn_run_id)
        page = await _external_boundary(
            session.list_tools
            if cursor is None
            else lambda cursor=cursor: session.list_tools(cursor=cursor)
        )
        for item in page.tools:
            if item.name in names or len(definitions) >= _MAX_MCP_DISCOVERED_TOOLS:
                raise _error("runtime_mcp_schema_drift", "MCP Tool 名称或 Schema 已漂移")
            names.add(item.name)
            definitions.append(item)
        cursor = page.nextCursor
        if cursor is None:
            return tuple(definitions)
        if cursor in seen_cursors:
            raise _error("runtime_mcp_schema_drift", "MCP Tool 名称或 Schema 已漂移")
        seen_cursors.add(cursor)
    raise _error("runtime_mcp_schema_drift", "MCP Tool 名称或 Schema 已漂移")


def _convert_allowed_tools(
    *,
    session: ClientSession,
    definitions: tuple[Tool, ...],
    allowed_raw_names: frozenset[str],
    server_name: str,
    interceptor: ToolCallInterceptor,
) -> tuple[BaseTool, ...]:
    """只转换已审核子集，未登记 Tool 从不进入 Deep Agent。"""

    try:
        return tuple(
            convert_mcp_tool_to_langchain_tool(
                session,
                item,
                server_name=server_name,
                tool_name_prefix=True,
                tool_interceptors=[interceptor],
                handle_tool_errors=False,
            )
            for item in definitions
            if item.name in allowed_raw_names
        )
    except ResearchAgentRuntimeError:
        raise
    except Exception:
        raise _error(
            "runtime_mcp_unavailable",
            "MCP 能力暂时不可用",
            RuntimeErrorKind.TEMPORARY,
        ) from None


async def _external_boundary[T](operation: Callable[[], Awaitable[T]]) -> T:
    """将 SDK/连接异常收敛为不含底层文本的 Runtime 错误。"""

    try:
        return await operation()
    except asyncio.CancelledError:
        raise
    except ResearchAgentRuntimeError:
        raise
    except Exception:
        raise _error(
            "runtime_mcp_unavailable",
            "MCP 能力暂时不可用",
            RuntimeErrorKind.TEMPORARY,
        ) from None


def _error(
    code: str,
    safe_message: str,
    kind: RuntimeErrorKind = RuntimeErrorKind.PERMANENT,
) -> ResearchAgentRuntimeError:
    return ResearchAgentRuntimeError(kind=kind, code=code, safe_message=safe_message)
