"""按 Runtime operation 组装 Deep Agents graph 与 Session Sandbox Workspace。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from typing import Protocol

from deepagents.backends import BackendProtocol, StateBackend
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntime,
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeResumeRequest,
    RuntimeTurnReconciliation,
    RuntimeTurnRequest,
    RuntimeTurnResult,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxWorkspaceLease,
    SandboxWorkspaceManager,
)
from literature_agent.infrastructure.agent.skill_backend import (
    SkillRuntimeMaterialization,
)


class CheckpointExecutionFactory(Protocol):
    """每次 Runtime operation 提供独立 Saver 实例。"""

    def create_saver(self) -> BaseCheckpointSaver[str]: ...


RuntimeFactory = Callable[
    [
        BaseCheckpointSaver[str],
        BackendProtocol,
        Callable[[RuntimeTurnRequest], Awaitable[None]] | None,
    ],
    ResearchAgentRuntime,
]
RuntimeWithToolsFactory = Callable[
    [
        BaseCheckpointSaver[str],
        BackendProtocol,
        Callable[[RuntimeTurnRequest], Awaitable[None]] | None,
        tuple[BaseTool, ...],
    ],
    ResearchAgentRuntime,
]
RuntimeWithCapabilitiesFactory = Callable[
    [
        BaseCheckpointSaver[str],
        BackendProtocol,
        Callable[[RuntimeTurnRequest], Awaitable[None]] | None,
        tuple[BaseTool, ...],
        SkillRuntimeMaterialization,
    ],
    ResearchAgentRuntime,
]


class RuntimeMcpToolLoader(Protocol):
    """仅在 execute/resume 期间打开并关闭 MCP ClientSession。"""

    def open(
        self, request: RuntimeTurnRequest, lease: SandboxWorkspaceLease
    ) -> AbstractAsyncContextManager[tuple[BaseTool, ...]]: ...


class RuntimeSkillMaterializer(Protocol):
    async def materialize(
        self, request: RuntimeTurnRequest
    ) -> SkillRuntimeMaterialization: ...


@dataclass(slots=True)
class _ActiveExecution:
    runtime: ResearchAgentRuntime
    lease: SandboxWorkspaceLease
    cancelled: bool = False
    runtime_entered: bool = False
    workspace_staged: bool = False


class SandboxedResearchAgentRuntime:
    """持有短生命周期 graph；只在 execute/resume 路径接触 Sandbox。"""

    def __init__(
        self,
        *,
        checkpoint_factory: CheckpointExecutionFactory,
        runtime_factory: RuntimeFactory,
        workspace_manager: SandboxWorkspaceManager,
        runtime_with_tools_factory: RuntimeWithToolsFactory | None = None,
        mcp_tool_loader: RuntimeMcpToolLoader | None = None,
        runtime_with_capabilities_factory: RuntimeWithCapabilitiesFactory | None = None,
        skill_materializer: RuntimeSkillMaterializer | None = None,
    ) -> None:
        self._checkpoint_factory = checkpoint_factory
        self._runtime_factory = runtime_factory
        self._workspace_manager = workspace_manager
        self._runtime_with_tools_factory = runtime_with_tools_factory
        self._mcp_tool_loader = mcp_tool_loader
        self._runtime_with_capabilities_factory = runtime_with_capabilities_factory
        self._skill_materializer = skill_materializer
        self._active: dict[str, _ActiveExecution] = {}
        self._active_lock = asyncio.Lock()

    def execute_turn(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        return self._execute_with_preflight(request)

    async def _execute_with_preflight(
        self, request: RuntimeTurnRequest
    ) -> AsyncIterator[RuntimeEvent]:
        """先用持久控制事实收敛重投，避免已知 Execution 抢占 Sandbox。"""
        offline = self._offline_runtime()
        try:
            await offline.reconcile_turn(request.turn_run_id)
        except ResearchAgentRuntimeError as exc:
            if exc.code != "runtime_turn_not_found":
                raise
        else:
            async for event in offline.execute_turn(request):
                yield event
            return
        async for event in self._run_with_workspace(request, resume=None):
            yield event

    def resume_turn(self, request: RuntimeResumeRequest) -> AsyncIterator[RuntimeEvent]:
        if request.turn_request is None:
            return self._offline_runtime().resume_turn(request)
        return self._run_with_workspace(request.turn_request, resume=request)

    async def _run_with_workspace(
        self,
        turn_request: RuntimeTurnRequest,
        *,
        resume: RuntimeResumeRequest | None,
    ) -> AsyncIterator[RuntimeEvent]:
        if turn_request.policy_snapshot.skill_refs:
            if self._skill_materializer is None:
                raise ResearchAgentRuntimeError(
                    kind=RuntimeErrorKind.PERMANENT,
                    code="runtime_skill_unavailable",
                    safe_message="本轮 Skill 能力未装配",
                )
            # 精确版本与 hash 先在短 DB 会话内复核，结束后才取得外部 Sandbox。
            skills = await self._skill_materializer.materialize(turn_request)
        else:
            skills = SkillRuntimeMaterialization(StateBackend(), ())
        lease = await self._workspace_manager.acquire(turn_request)
        active_ref: _ActiveExecution | None = None
        tool_stack = AsyncExitStack()
        try:
            if turn_request.policy_snapshot.mcp_refs:
                if self._mcp_tool_loader is None or self._runtime_with_tools_factory is None:
                    raise ResearchAgentRuntimeError(
                        kind=RuntimeErrorKind.PERMANENT,
                        code="runtime_mcp_unavailable",
                        safe_message="本轮 MCP 能力未装配",
                    )
                mcp_tools = await tool_stack.enter_async_context(
                    self._mcp_tool_loader.open(turn_request, lease)
                )
            else:
                mcp_tools = ()
        except BaseException:
            await _close_runtime_resources(tool_stack, lease.backend)
            raise

        async def finalize(request: RuntimeTurnRequest) -> None:
            await self._workspace_manager.stage_snapshot(request, lease)
            assert active_ref is not None
            active_ref.workspace_staged = True

        try:
            runtime = self._new_runtime(
                lease.backend, before_succeed=finalize, tools=mcp_tools, skills=skills
            )
        except BaseException:
            await _close_runtime_resources(tool_stack, lease.backend)
            raise
        active = _ActiveExecution(runtime=runtime, lease=lease)
        active_ref = active
        duplicate = False
        async with self._active_lock:
            if turn_request.turn_run_id in self._active:
                duplicate = True
            else:
                self._active[turn_request.turn_run_id] = active
        if duplicate:
            await _close_runtime_resources(tool_stack, lease.backend)
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.TEMPORARY,
                code="runtime_turn_already_active",
                safe_message="同一 Runtime Turn 已在执行",
            )

        snapshot_committed = False
        try:
            stream = (
                runtime.execute_turn(turn_request)
                if resume is None
                else runtime.resume_turn(resume)
            )
            async for event in stream:
                active.runtime_entered = True
                if event.kind is RuntimeEventKind.COMPLETED:
                    if active.cancelled:
                        raise ResearchAgentRuntimeError(
                            kind=RuntimeErrorKind.CANCELLED,
                            code="runtime_turn_cancelled",
                            safe_message="Turn 已取消",
                        )
                    if not active.workspace_staged:
                        await self._workspace_manager.stage_snapshot(turn_request, lease)
                        active.workspace_staged = True
                    snapshot_committed = True
                yield event
        except BaseException as exc:
            preserve_for_finalization_retry = (
                isinstance(exc, ResearchAgentRuntimeError)
                and exc.kind is RuntimeErrorKind.TEMPORARY
                and exc.code
                in {
                    "runtime_workspace_snapshot_failed",
                }
            )
            permanent_snapshot_invalid = (
                isinstance(exc, ResearchAgentRuntimeError)
                and exc.code == "runtime_workspace_snapshot_invalid"
            )
            if (active.runtime_entered or permanent_snapshot_invalid) and not (
                snapshot_committed
                or preserve_for_finalization_retry
            ):
                await self._workspace_manager.mark_dirty(lease)
            raise
        finally:
            async with self._active_lock:
                self._active.pop(turn_request.turn_run_id, None)
            await _close_runtime_resources(tool_stack, lease.backend)

    async def cancel_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        async with self._active_lock:
            active = self._active.get(turn_run_id)
            if active is not None:
                active.cancelled = True
        if active is None:
            return await self._offline_runtime().cancel_turn(turn_run_id)
        reconciliation = await active.runtime.cancel_turn(turn_run_id)
        if active.runtime_entered:
            await self._workspace_manager.mark_dirty(active.lease)
        return reconciliation

    async def reconcile_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        """对账只读 PostgreSQL control/checkpoint，不连接或创建 Sandbox。"""
        async with self._active_lock:
            active = self._active.get(turn_run_id)
        runtime = active.runtime if active is not None else self._offline_runtime()
        return await runtime.reconcile_turn(turn_run_id)

    async def collect_turn_result(self, turn_run_id: str) -> RuntimeTurnResult:
        """成功结果收集不依赖 Sandbox Lease 仍然存活。"""
        async with self._active_lock:
            active = self._active.get(turn_run_id)
        runtime = active.runtime if active is not None else self._offline_runtime()
        return await runtime.collect_turn_result(turn_run_id)

    def _offline_runtime(self) -> ResearchAgentRuntime:
        return self._new_runtime(StateBackend())

    def _new_runtime(
        self,
        backend: BackendProtocol,
        *,
        before_succeed: Callable[[RuntimeTurnRequest], Awaitable[None]] | None = None,
        tools: tuple[BaseTool, ...] = (),
        skills: SkillRuntimeMaterialization | None = None,
    ) -> ResearchAgentRuntime:
        if skills is not None and skills.sources:
            if self._runtime_with_capabilities_factory is None:
                raise ResearchAgentRuntimeError(
                    kind=RuntimeErrorKind.PERMANENT,
                    code="runtime_skill_unavailable",
                    safe_message="本轮 Skill 能力未装配",
                )
            return self._runtime_with_capabilities_factory(
                self._checkpoint_factory.create_saver(),
                backend,
                before_succeed,
                tools,
                skills,
            )
        if tools:
            assert self._runtime_with_tools_factory is not None
            return self._runtime_with_tools_factory(
                self._checkpoint_factory.create_saver(),
                backend,
                before_succeed,
                tools,
            )
        return self._runtime_factory(
            self._checkpoint_factory.create_saver(),
            backend,
            before_succeed,
        )


async def _close_backend(backend: object) -> None:
    close = getattr(backend, "close", None)
    if callable(close):
        await asyncio.to_thread(close)


async def _close_runtime_resources(tool_stack: AsyncExitStack, backend: object) -> None:
    """MCP 关闭失败也必须尝试释放当前 Sandbox 连接。"""
    try:
        await tool_stack.aclose()
    finally:
        await _close_backend(backend)
