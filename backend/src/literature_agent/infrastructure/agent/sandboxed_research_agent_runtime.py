"""按 Runtime operation 组装 Deep Agents graph 与 Session Sandbox Workspace。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from deepagents.backends import BackendProtocol, StateBackend
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
    ) -> None:
        self._checkpoint_factory = checkpoint_factory
        self._runtime_factory = runtime_factory
        self._workspace_manager = workspace_manager
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
        lease = await self._workspace_manager.acquire(turn_request)
        active_ref: _ActiveExecution | None = None

        async def finalize(request: RuntimeTurnRequest) -> None:
            await self._workspace_manager.stage_snapshot(request, lease)
            assert active_ref is not None
            active_ref.workspace_staged = True

        runtime = self._new_runtime(lease.backend, before_succeed=finalize)
        active = _ActiveExecution(runtime=runtime, lease=lease)
        active_ref = active
        async with self._active_lock:
            if turn_request.turn_run_id in self._active:
                await _close_backend(lease.backend)
                raise ResearchAgentRuntimeError(
                    kind=RuntimeErrorKind.TEMPORARY,
                    code="runtime_turn_already_active",
                    safe_message="同一 Runtime Turn 已在执行",
                )
            self._active[turn_request.turn_run_id] = active

        snapshot_committed = False
        stream = (
            runtime.execute_turn(turn_request)
            if resume is None
            else runtime.resume_turn(resume)
        )
        try:
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
            await _close_backend(lease.backend)

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
    ) -> ResearchAgentRuntime:
        return self._runtime_factory(
            self._checkpoint_factory.create_saver(),
            backend,
            before_succeed,
        )


async def _close_backend(backend: object) -> None:
    close = getattr(backend, "close", None)
    if callable(close):
        await asyncio.to_thread(close)
