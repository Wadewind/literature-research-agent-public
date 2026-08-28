"""submit_artifact 只在真实 Sandbox Turn 中接收 ToolRuntime fence。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from deepagents.backends.protocol import FileDownloadResponse
from langchain.tools import ToolRuntime

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeTurnRequest,
)
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
)
from literature_agent.domain.agent_artifact import AgentArtifactValidationError
from literature_agent.domain.research_agent import (
    create_agent_artifact_candidate,
    create_context_snapshot,
    create_project_research_workspace_policy_snapshot,
)
from literature_agent.domain.runtime_execution import RuntimeExecutionPermit
from literature_agent.infrastructure.agent.artifact_tools import (
    AgentArtifactToolFactory,
    SandboxAgentArtifactSource,
)
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    _tool_schema_hash,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseRecord,
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
)


def _request() -> RuntimeTurnRequest:
    return RuntimeTurnRequest(
        "session-1",
        "turn-1",
        "message-1",
        "生成图表",
        create_context_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            user_message_id="message-1",
            history_through_sequence=0,
        ),
        create_project_research_workspace_policy_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
        ),
    )


def _lease() -> SandboxWorkspaceLease:
    now = datetime.now(UTC)
    return SandboxWorkspaceLease(
        SandboxLeaseRecord(
            "session-1",
            "owner-1",
            "project-1",
            "turn-1",
            "sandbox-1",
            "image:v1",
            1,
            1,
            SandboxLeaseStatus.ACTIVE,
            now,
            now + timedelta(minutes=5),
            now,
        ),
        SimpleNamespace(),
    )


class _Service:
    calls = []

    async def submit(self, **kwargs):
        self.calls.append(kwargs)
        return create_agent_artifact_candidate(
            candidate_id="candidate-1",
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            name="chart.png",
            media_type="image/png",
            content_ref="/workspace/outputs/chart.png",
            content_hash="a" * 64,
            size_bytes=24,
        ).validate(
            tool_call_id="call-1",
            storage_key="private/key",
            sandbox_generation=1,
            sandbox_fencing_token=1,
        )


def _runtime(*, tool_call_id="call-1", turn_run_id="turn-1"):
    return ToolRuntime(
        state={},
        context=SimpleNamespace(
            turn_run_id=turn_run_id,
            runtime_permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
        ),
        config={},
        stream_writer=lambda _: None,
        tool_call_id=tool_call_id,
        store=None,
    )


def test_submit_artifact_schema_matches_frozen_tool_policy_ref() -> None:
    request = _request()
    lease = _lease()
    value = AgentArtifactToolFactory(
        _Service(), _WorkspaceRepository(lease.record)
    ).create(request, lease)[0]
    expected = next(
        ref for ref in request.policy_snapshot.tool_refs if ref.name == "submit_artifact"
    )

    assert _tool_schema_hash(value) == expected.input_schema_hash


class _WorkspaceRepository:
    def __init__(self, record):
        self.record = record

    async def get_lease(self, session_id):
        return self.record


class _Backend:
    def __init__(self, entry_type="file"):
        self.entry_type = entry_type

    def list_workspace_files(self):
        return [("/workspace/outputs/chart.png", self.entry_type, 24)]

    def download_files(self, paths):
        return [FileDownloadResponse(path=paths[0], content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)]


@pytest.mark.asyncio
async def test_submit_artifact_tool_passes_stable_tool_call_and_scope() -> None:
    service = _Service()
    lease = _lease()
    tool = AgentArtifactToolFactory(service, _WorkspaceRepository(lease.record)).create(
        _request(), lease
    )[0]

    result = await tool.coroutine(
        path="/workspace/outputs/chart.png",
        name="chart.png",
        media_type="image/png",
        runtime=_runtime(),
    )

    assert tool.name == "submit_artifact"
    assert '"status":"validated"' in result
    assert service.calls[0]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_submit_artifact_tool_rejects_missing_tool_call_id() -> None:
    lease = _lease()
    tool = AgentArtifactToolFactory(_Service(), _WorkspaceRepository(lease.record)).create(
        _request(), lease
    )[0]
    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await tool.coroutine(
            path="/workspace/outputs/chart.png",
            name="chart.png",
            media_type="image/png",
            runtime=_runtime(tool_call_id=None),
        )
    assert caught.value.code == "artifact_runtime_context_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_error", "expected_kind"),
    [
        (
            RuntimeExecutionControlError(
                "runtime_turn_cancelled", "业务 Turn 已请求取消"
            ),
            RuntimeErrorKind.CANCELLED,
        ),
        (
            RuntimeExecutionControlError(
                "runtime_execution_lease_lost",
                "Runtime Execution lease 已失效",
                temporary=True,
            ),
            RuntimeErrorKind.TEMPORARY,
        ),
        (
            RuntimeExecutionControlError(
                "runtime_execution_fence_invalid", "Runtime Execution fence 非法"
            ),
            RuntimeErrorKind.PERMANENT,
        ),
    ],
)
async def test_submit_artifact_tool_preserves_runtime_control_error_kind(
    control_error: RuntimeExecutionControlError,
    expected_kind: RuntimeErrorKind,
) -> None:
    class _ControlFailingService:
        async def submit(self, **kwargs):
            del kwargs
            raise control_error

    lease = _lease()
    tool = AgentArtifactToolFactory(
        _ControlFailingService(), _WorkspaceRepository(lease.record)
    ).create(_request(), lease)[0]

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await tool.coroutine(
            path="/workspace/outputs/chart.png",
            name="chart.png",
            media_type="image/png",
            runtime=_runtime(),
        )

    assert caught.value.kind is expected_kind
    assert caught.value.code == control_error.code
    assert caught.value.safe_message == control_error.safe_message


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_type", ["directory", "symlink", "device"])
async def test_sandbox_artifact_source_rejects_non_regular_entry(entry_type: str) -> None:
    lease = _lease()
    lease = SandboxWorkspaceLease(lease.record, _Backend(entry_type))
    source = SandboxAgentArtifactSource(lease, _WorkspaceRepository(lease.record))
    with pytest.raises(AgentArtifactValidationError) as caught:
        await source.read_regular_file("/workspace/outputs/chart.png", max_bytes=10 * 1024 * 1024)
    assert caught.value.code == "artifact_file_not_regular"
