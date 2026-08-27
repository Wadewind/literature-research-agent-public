"""Sandbox Runtime 装配边界的离线测试。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from deepagents.backends import StateBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeExecutionState,
    RuntimeResumeRequest,
    RuntimeTurnReconciliation,
    RuntimeTurnRequest,
    RuntimeTurnResult,
)
from literature_agent.domain.mcp_configuration import McpPolicyRef, McpPolicyToolRef
from literature_agent.domain.research_agent import (
    RuntimeSessionBinding,
    RuntimeTurnBinding,
    create_context_snapshot,
    create_project_research_workspace_policy_snapshot,
)
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    DeepAgentsResearchAgentRuntime,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseRecord,
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
)
from literature_agent.infrastructure.agent.sandboxed_research_agent_runtime import (
    SandboxedResearchAgentRuntime,
)
from tests.fakes.deep_agent_model import ScriptedDeepAgentChatModel


def _request() -> RuntimeTurnRequest:
    context = create_context_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        user_message_id="message-1",
        history_through_sequence=0,
        review_output_id="review-output-1",
    )
    policy = create_project_research_workspace_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
    )
    return RuntimeTurnRequest(
        session_id="session-1",
        turn_run_id="turn-1",
        user_message_id="message-1",
        user_message_content="分析项目证据",
        context_snapshot=context,
        policy_snapshot=policy,
    )


def _mcp_request() -> RuntimeTurnRequest:
    request = _request()
    ref = McpPolicyRef(
        profile_id="profile-1",
        profile_revision=1,
        catalog_id="fixture-search",
        version="1.0.0",
        config_hash="a" * 64,
        tools=(McpPolicyToolRef("fixture-search_search", "b" * 64),),
    )
    return replace(
        request,
        policy_snapshot=create_project_research_workspace_policy_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            mcp_refs=(ref,),
        ),
    )


class _Backend:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _CheckpointFactory:
    def __init__(self) -> None:
        self.savers: list[MemorySaver] = []

    def create_saver(self) -> MemorySaver:
        saver = MemorySaver()
        self.savers.append(saver)
        return saver


class _SharedCheckpointFactory:
    """模拟 pool：Saver 实例不同，但共享同一持久存储。"""

    def __init__(self) -> None:
        self.master = MemorySaver()
        self.savers: list[MemorySaver] = []

    def create_saver(self) -> MemorySaver:
        saver = MemorySaver()
        saver.storage = self.master.storage
        saver.writes = self.master.writes
        saver.blobs = self.master.blobs
        self.savers.append(saver)
        return saver


class _ExecuteBackend(BaseSandbox):
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    @property
    def id(self) -> str:
        return "sandbox-integration"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        self.commands.append(command)
        return ExecuteResponse(output="done", exit_code=0, truncated=False)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, content=b"") for path in paths]

    def close(self) -> None:
        self.closed = True


class _ExecuteThenAnswerModel(ScriptedDeepAgentChatModel):
    def _next_message(self, messages: list[Any]) -> AIMessage:
        self.model_call_count += 1
        if any(isinstance(item, ToolMessage) for item in messages):
            return AIMessage(content="当前授权上下文证据不足。")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "python -c 'print(1)'"},
                    "id": "sandbox-execute-1",
                    "type": "tool_call",
                }
            ],
        )


class _WorkspaceManager:
    def __init__(self) -> None:
        self.backend = _Backend()
        self.acquire_calls = 0
        self.commit_calls = 0
        self.dirty_calls = 0

    async def acquire(self, request: RuntimeTurnRequest) -> SandboxWorkspaceLease:
        self.acquire_calls += 1
        now = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        return SandboxWorkspaceLease(
            record=SandboxLeaseRecord(
                session_id=request.session_id,
                owner_id=request.context_snapshot.owner_id,
                project_id=request.context_snapshot.project_id,
                holder_turn_run_id=request.turn_run_id,
                sandbox_id="sandbox-1",
                image_ref="image@sha256:test",
                generation=1,
                fencing_token=1,
                status=SandboxLeaseStatus.ACTIVE,
                generation_started_at=now,
                expires_at=now + timedelta(minutes=10),
                updated_at=now,
            ),
            backend=self.backend,
        )

    async def commit_success(self, request: Any, lease: Any) -> object:
        del request, lease
        self.commit_calls += 1
        return object()

    async def stage_snapshot(self, request: Any, lease: Any) -> object:
        return await self.commit_success(request, lease)

    async def mark_dirty(self, lease: Any) -> None:
        del lease
        self.dirty_calls += 1


class _ExecuteWorkspaceManager(_WorkspaceManager):
    def __init__(self) -> None:
        super().__init__()
        self.backend = _ExecuteBackend()


class _FailingFinalizeWorkspaceManager(_ExecuteWorkspaceManager):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def commit_success(self, request: Any, lease: Any) -> object:
        del request, lease
        self.commit_calls += 1
        raise self.error

    async def stage_snapshot(self, request: Any, lease: Any) -> object:
        return await self.commit_success(request, lease)


class _MultiBackendWorkspaceManager(_WorkspaceManager):
    def __init__(self) -> None:
        super().__init__()
        self.backends: list[_Backend] = []

    async def acquire(self, request: RuntimeTurnRequest) -> SandboxWorkspaceLease:
        lease = await super().acquire(request)
        backend = _Backend()
        self.backends.append(backend)
        return SandboxWorkspaceLease(record=lease.record, backend=backend)


class _Runtime:
    def __init__(
        self, backend: object, *, fail: bool = False, known: dict[str, bool] | None = None
    ) -> None:
        self.backend = backend
        self.fail = fail
        self.known = known if known is not None else {"value": True}

    async def execute_turn(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        if self.fail:
            raise RuntimeError("runtime failed")
        self.known["value"] = True
        yield RuntimeEvent("event-1", request.turn_run_id, 1, RuntimeEventKind.COMPLETED)

    def resume_turn(self, request: Any) -> AsyncIterator[RuntimeEvent]:
        raise AssertionError(request)

    async def cancel_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        return self._reconciliation(turn_run_id)

    async def reconcile_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        if not self.known["value"]:
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.PERMANENT,
                code="runtime_turn_not_found",
                safe_message="Runtime Turn 不存在",
            )
        return self._reconciliation(turn_run_id)

    async def collect_turn_result(self, turn_run_id: str) -> RuntimeTurnResult:
        return RuntimeTurnResult(turn_run_id=turn_run_id, assistant_content="result")

    @staticmethod
    def _reconciliation(turn_run_id: str) -> RuntimeTurnReconciliation:
        return RuntimeTurnReconciliation(
            turn_run_id=turn_run_id,
            state=RuntimeExecutionState.SUCCEEDED,
            session_binding=RuntimeSessionBinding(
                "session-1", "binding-1", 1, "thread-1", "workspace-1"
            ),
            turn_binding=RuntimeTurnBinding(
                "session-1", turn_run_id, "binding-1", "execution-1", "checkpoint-1"
            ),
            last_event_sequence=1,
            result_available=True,
        )


async def test_completed_collect_and_reconcile_do_not_reacquire_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def immediate_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "literature_agent.infrastructure.agent.sandboxed_research_agent_runtime.asyncio.to_thread",
        immediate_to_thread,
    )
    checkpoints = _CheckpointFactory()
    workspace = _WorkspaceManager()
    backends: list[object] = []
    known = {"value": False}

    def runtime_factory(
        saver: Any, backend: object, before_succeed: Any
    ) -> _Runtime:
        del saver, before_succeed
        backends.append(backend)
        return _Runtime(backend, known=known)

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=checkpoints,
        runtime_factory=runtime_factory,  # type: ignore[arg-type]
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    events = [item async for item in runtime.execute_turn(_request())]
    reconciliation = await runtime.reconcile_turn("turn-1")
    result = await runtime.collect_turn_result("turn-1")

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert reconciliation.state is RuntimeExecutionState.SUCCEEDED
    assert result.assistant_content == "result"
    assert workspace.acquire_calls == 1
    assert workspace.commit_calls == 1
    assert workspace.dirty_calls == 0
    assert workspace.backend.closed is True
    assert len({id(item) for item in checkpoints.savers}) == 4
    assert isinstance(backends[0], StateBackend)
    assert isinstance(backends[2], StateBackend)
    assert isinstance(backends[3], StateBackend)


async def test_pre_event_claim_rejection_does_not_mark_workspace_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def immediate_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "literature_agent.infrastructure.agent.sandboxed_research_agent_runtime.asyncio.to_thread",
        immediate_to_thread,
    )
    workspace = _WorkspaceManager()
    known = {"value": False}
    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=lambda saver, backend, before_succeed: _Runtime(
            backend, fail=True, known=known
        ),
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="runtime failed"):
        _ = [item async for item in runtime.execute_turn(_request())]

    assert workspace.dirty_calls == 0
    assert workspace.commit_calls == 0
    assert workspace.backend.closed is True


async def test_known_completed_turn_replays_without_acquiring_sandbox() -> None:
    workspace = _WorkspaceManager()

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=lambda saver, backend, before_succeed: _Runtime(
            backend, known={"value": True}
        ),
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    events = [item async for item in runtime.execute_turn(_request())]

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert workspace.acquire_calls == 0
    assert workspace.commit_calls == 0
    assert workspace.dirty_calls == 0


class _StartedThenFailRuntime(_Runtime):
    async def execute_turn(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        self.known["value"] = True
        yield RuntimeEvent("event-1", request.turn_run_id, 1, RuntimeEventKind.STARTED)
        raise RuntimeError("runtime failed after start")


class _BlockingRuntime(_Runtime):
    def __init__(self, backend: object, *, entered: asyncio.Event, release: asyncio.Event):
        super().__init__(backend, known={"value": False})
        self.entered = entered
        self.release = release

    async def execute_turn(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        self.entered.set()
        await self.release.wait()
        yield RuntimeEvent("event-1", request.turn_run_id, 1, RuntimeEventKind.COMPLETED)


async def test_post_event_failure_marks_workspace_dirty() -> None:
    workspace = _WorkspaceManager()
    known = {"value": False}
    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=lambda saver, backend, before_succeed: _StartedThenFailRuntime(
            backend, known=known
        ),
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="after start"):
        _ = [item async for item in runtime.execute_turn(_request())]

    assert workspace.dirty_calls == 1


async def test_local_pre_event_duplicate_closes_only_its_connection_without_dirty() -> None:
    workspace = _MultiBackendWorkspaceManager()
    entered, release = asyncio.Event(), asyncio.Event()

    def runtime_factory(
        saver: Any, backend: object, before_succeed: Any
    ) -> _Runtime:
        del saver, before_succeed
        if isinstance(backend, StateBackend):
            return _Runtime(backend, known={"value": False})
        return _BlockingRuntime(backend, entered=entered, release=release)

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=runtime_factory,  # type: ignore[arg-type]
        workspace_manager=workspace,  # type: ignore[arg-type]
    )
    first = asyncio.create_task(
        _collect_events(runtime.execute_turn(_request()))
    )
    await entered.wait()

    with pytest.raises(ResearchAgentRuntimeError) as error:
        await _collect_events(runtime.execute_turn(_request()))
    assert error.value.code == "runtime_turn_already_active"
    assert workspace.dirty_calls == 0
    assert workspace.backends[0].closed is False
    assert workspace.backends[1].closed is True

    release.set()
    await first
    assert workspace.backends[0].closed is True


async def _collect_events(stream: AsyncIterator[RuntimeEvent]) -> list[RuntimeEvent]:
    return [item async for item in stream]


async def test_real_deep_agent_completed_state_is_collected_without_live_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def immediate_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "literature_agent.infrastructure.agent.sandboxed_research_agent_runtime.asyncio.to_thread",
        immediate_to_thread,
    )
    checkpoints = _SharedCheckpointFactory()
    workspace = _ExecuteWorkspaceManager()
    model = _ExecuteThenAnswerModel()

    def runtime_factory(
        saver: Any, backend: Any, before_succeed: Any
    ) -> DeepAgentsResearchAgentRuntime:
        return DeepAgentsResearchAgentRuntime(
            model=model,
            checkpointer=saver,
            backend=backend,
            before_succeed=before_succeed,
        )

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=checkpoints,
        runtime_factory=runtime_factory,
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    events = [item async for item in runtime.execute_turn(_request())]
    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert workspace.backend.commands == ["python -c 'print(1)'"]
    assert workspace.backend.closed is True

    result = await runtime.collect_turn_result("turn-1")
    reconciliation = await runtime.reconcile_turn("turn-1")

    assert result.assistant_content == "当前授权上下文证据不足。"
    assert reconciliation.state is RuntimeExecutionState.SUCCEEDED
    assert workspace.acquire_calls == 1
    assert len({id(item) for item in checkpoints.savers}) == 4


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_dirty"),
    [
        (RuntimeError("storage unavailable"), "runtime_workspace_snapshot_failed", 0),
        (ValueError("symlink"), "runtime_workspace_snapshot_invalid", 1),
    ],
)
async def test_workspace_finalizer_failure_controls_lease_dirty_policy(
    error: Exception, expected_code: str, expected_dirty: int
) -> None:
    workspace = _FailingFinalizeWorkspaceManager(error)

    def runtime_factory(
        saver: Any, backend: Any, before_succeed: Any
    ) -> DeepAgentsResearchAgentRuntime:
        return DeepAgentsResearchAgentRuntime(
            model=_ExecuteThenAnswerModel(),
            checkpointer=saver,
            backend=backend,
            before_succeed=before_succeed,
        )

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_SharedCheckpointFactory(),
        runtime_factory=runtime_factory,
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    with pytest.raises(ResearchAgentRuntimeError) as failure:
        await _collect_events(runtime.execute_turn(_request()))

    assert failure.value.code == expected_code
    assert workspace.commit_calls == 1
    assert workspace.dirty_calls == expected_dirty


async def test_pre_event_checkpoint_finalizer_invalid_marks_acquired_lease_dirty() -> None:
    workspace = _WorkspaceManager()

    class _PreEventInvalidRuntime(_Runtime):
        def resume_turn(self, request: Any) -> AsyncIterator[RuntimeEvent]:
            async def stream() -> AsyncIterator[RuntimeEvent]:
                raise ResearchAgentRuntimeError(
                    kind=RuntimeErrorKind.PERMANENT,
                    code="runtime_workspace_snapshot_invalid",
                    safe_message="WorkspaceSnapshot 安全校验失败",
                )
                yield  # pragma: no cover

            return stream()

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=lambda saver, backend, before_succeed: (
            _PreEventInvalidRuntime(backend)
        ),
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    with pytest.raises(ResearchAgentRuntimeError) as failure:
        await _collect_events(
            runtime.resume_turn(
                RuntimeResumeRequest(
                    turn_run_id="turn-1",
                    response=None,
                    turn_request=_request(),
                )
            )
        )

    assert failure.value.code == "runtime_workspace_snapshot_invalid"
    assert workspace.dirty_calls == 1


@pytest.mark.asyncio
async def test_mcp_session_wraps_runtime_execution_and_closes_before_sandbox() -> None:
    workspace = _WorkspaceManager()
    known = {"value": False}
    lifecycle: list[str] = []

    @tool
    def fixture_search_search(query: str) -> str:
        """确定性测试工具。"""
        return query

    class _Loader:
        @asynccontextmanager
        async def open(self, request, lease):
            assert request.turn_run_id == "turn-1"
            assert lease.backend is workspace.backend
            lifecycle.append("mcp-open")
            try:
                yield (fixture_search_search,)
            finally:
                assert workspace.backend.closed is False
                lifecycle.append("mcp-close")

    def runtime_factory(saver, backend, before_succeed):
        del saver, before_succeed
        return _Runtime(backend, known=known)

    def runtime_with_tools_factory(saver, backend, before_succeed, tools):
        del saver, before_succeed
        assert [item.name for item in tools] == ["fixture_search_search"]
        lifecycle.append("runtime-created")
        return _Runtime(backend, known=known)

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=runtime_factory,
        runtime_with_tools_factory=runtime_with_tools_factory,
        mcp_tool_loader=_Loader(),
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    events = [event async for event in runtime.execute_turn(_mcp_request())]

    assert events[-1].kind is RuntimeEventKind.COMPLETED
    assert lifecycle == ["mcp-open", "runtime-created", "mcp-close"]
    assert workspace.backend.closed is True


@pytest.mark.asyncio
async def test_mcp_session_and_sandbox_close_when_runtime_factory_fails() -> None:
    workspace = _WorkspaceManager()
    lifecycle: list[str] = []

    @tool
    def fixture_search_search(query: str) -> str:
        """确定性测试工具。"""
        return query

    class _Loader:
        @asynccontextmanager
        async def open(self, request, lease):
            del request, lease
            lifecycle.append("mcp-open")
            try:
                yield (fixture_search_search,)
            finally:
                assert workspace.backend.closed is False
                lifecycle.append("mcp-close")

    def failed_factory(saver, backend, before_succeed, tools):
        del saver, backend, before_succeed, tools
        raise RuntimeError("runtime factory failed")

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=lambda saver, backend, before_succeed: _Runtime(
            backend, known={"value": False}
        ),
        runtime_with_tools_factory=failed_factory,
        mcp_tool_loader=_Loader(),
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="runtime factory failed"):
        await _collect_events(runtime.execute_turn(_mcp_request()))

    assert lifecycle == ["mcp-open", "mcp-close"]
    assert workspace.backend.closed is True


@pytest.mark.asyncio
async def test_mcp_close_failure_still_closes_sandbox_connection() -> None:
    workspace = _WorkspaceManager()
    known = {"value": False}

    @tool
    def fixture_search_search(query: str) -> str:
        """确定性测试工具。"""
        return query

    class _Loader:
        @asynccontextmanager
        async def open(self, request, lease):
            del request, lease
            try:
                yield (fixture_search_search,)
            finally:
                raise RuntimeError("mcp close failed")

    runtime = SandboxedResearchAgentRuntime(
        checkpoint_factory=_CheckpointFactory(),
        runtime_factory=lambda saver, backend, before_succeed: _Runtime(
            backend, known=known
        ),
        runtime_with_tools_factory=(
            lambda saver, backend, before_succeed, tools: _Runtime(
                backend, known=known
            )
        ),
        mcp_tool_loader=_Loader(),
        workspace_manager=workspace,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="mcp close failed"):
        await _collect_events(runtime.execute_turn(_mcp_request()))

    assert workspace.backend.closed is True
