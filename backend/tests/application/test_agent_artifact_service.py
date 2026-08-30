"""Sandbox 文件校验与 Storage 写入保持在 Candidate 短事务之外。"""

import hashlib
from types import SimpleNamespace

import pytest

from literature_agent.application.agent_artifact_service import (
    AgentArtifactServiceError,
    AgentArtifactSubmissionService,
    _safe_rejected_candidate_value,
)
from literature_agent.application.ports.agent_artifact_source import AgentArtifactSourceScope
from literature_agent.application.ports.research_agent_runtime import RuntimeTurnRequest
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
)
from literature_agent.domain.research_agent import (
    create_context_snapshot,
    create_project_research_workspace_policy_snapshot,
)
from literature_agent.domain.runtime_execution import RuntimeExecutionPermit


def _request() -> RuntimeTurnRequest:
    return RuntimeTurnRequest(
        session_id="session-1",
        turn_run_id="turn-1",
        user_message_id="message-1",
        user_message_content="请绘制图表",
        context_snapshot=create_context_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            user_message_id="message-1",
            history_through_sequence=0,
        ),
        policy_snapshot=create_project_research_workspace_policy_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
        ),
    )


def test_rejected_candidate_attempt_value_preserves_safe_name_and_removes_controls() -> None:
    assert (
        _safe_rejected_candidate_value("plot_quadratic.py", fallback="invalid-name")
        == "plot_quadratic.py"
    )
    assert (
        _safe_rejected_candidate_value("../bad\nname.py", fallback="invalid-name")
        == "..�bad�name.py"
    )
    assert _safe_rejected_candidate_value("\x00", fallback="invalid-name") == "�"


class _Source:
    scope = AgentArtifactSourceScope("owner-1", "project-1", "session-1", "turn-1", 1, 1)

    def __init__(self, content: bytes) -> None:
        self.content = content

    async def assert_current(self):
        return None

    async def read_regular_file(self, path, *, max_bytes):
        assert path == "/workspace/outputs/chart.png"
        assert len(self.content) <= max_bytes
        return self.content


class _StaleSource(_Source):
    async def assert_current(self):
        raise AgentArtifactServiceError(
            "artifact_sandbox_fence_lost", "Artifact Sandbox generation/fence 已失效"
        )


class _Storage:
    def __init__(self) -> None:
        self.values = {}

    async def write(self, key, content):
        self.values[key] = content


class _Service(AgentArtifactSubmissionService):
    def __init__(self, storage, *, source_resolver=None):
        super().__init__(
            session_factory=lambda: None,
            run_repo_factory=lambda _: None,
            agent_repo_factory=lambda _: None,
            event_repo_factory=lambda _: None,
            storage=storage,
            execution_control=SimpleNamespace(),
            source_resolver=source_resolver,
        )
        self.recorded = None
        self.rejected = None

    async def _assert_current(self, request, permit, expected_lease):
        return None

    async def _record_candidate(self, request, lease, candidate):
        self.recorded = candidate
        return candidate

    async def _record_rejection(self, **kwargs):
        self.rejected = kwargs


class _FailingCommitService(_Service):
    async def _record_candidate(self, request, source_scope, candidate):
        raise RuntimeError("db_commit_failed")


@pytest.mark.asyncio
async def test_submit_validates_download_and_records_only_small_fact() -> None:
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    storage = _Storage()
    service = _Service(storage)

    candidate = await service.submit(
        request=_request(),
        permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
        source=_Source(content),
        tool_call_id="call-1",
        path="/workspace/outputs/chart.png",
        name="chart.png",
        media_type="image/png",
    )

    assert candidate.status.value == "validated"
    assert candidate.content_hash == hashlib.sha256(content).hexdigest()
    assert list(storage.values.values()) == [content]
    assert service.recorded == candidate


@pytest.mark.asyncio
async def test_submit_rejection_keeps_attempted_name_type_and_stable_code() -> None:
    service = _Service(_Storage())

    with pytest.raises(AgentArtifactServiceError) as caught:
        await service.submit(
            request=_request(),
            permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
            source=_Source(b"print('quadratic')\n"),
            tool_call_id="call-invalid-type",
            path="/workspace/outputs/plot_quadratic.py",
            name="plot_quadratic.py",
            media_type="text/plain",
        )

    assert caught.value.code == "artifact_extension_mismatch"
    assert service.rejected["name"] == "plot_quadratic.py"
    assert service.rejected["media_type"] == "text/plain"
    assert service.rejected["code"] == "artifact_extension_mismatch"


@pytest.mark.asyncio
async def test_submit_validates_and_freezes_public_source_before_file_io() -> None:
    class _Resolver:
        async def resolve(self, hostname, port):
            assert (hostname, port) == ("arxiv.org", 443)
            return ("151.101.3.42",)

    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    storage = _Storage()
    service = _Service(storage, source_resolver=_Resolver())

    candidate = await service.submit(
        request=_request(),
        permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
        source=_Source(content),
        tool_call_id="call-source",
        path="/workspace/outputs/chart.png",
        name="chart.png",
        media_type="image/png",
        source_url="https://arxiv.org/pdf/2401.00001?token=must-not-persist",
    )

    assert candidate.source_url == "https://arxiv.org/pdf/2401.00001"
    assert candidate.source_url_hash is not None
    assert "token" not in candidate.source_url


@pytest.mark.asyncio
async def test_submit_rejects_private_dns_answer_before_file_and_storage_io() -> None:
    class _Resolver:
        async def resolve(self, hostname, port):
            return ("93.184.216.34", "127.0.0.1")

    class _UnreadableSource(_Source):
        async def read_regular_file(self, path, *, max_bytes):
            raise AssertionError("恶意来源不得读取 Sandbox 文件")

    storage = _Storage()
    service = _Service(storage, source_resolver=_Resolver())
    with pytest.raises(AgentArtifactServiceError) as caught:
        await service.submit(
            request=_request(),
            permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
            source=_UnreadableSource(b""),
            tool_call_id="call-private-source",
            path="/workspace/outputs/chart.png",
            name="chart.png",
            media_type="image/png",
            source_url="https://example.com/paper",
        )

    assert caught.value.code == "source_target_forbidden"
    assert storage.values == {}


@pytest.mark.asyncio
async def test_storage_success_db_failure_keeps_only_staging_blob_for_retry() -> None:
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    storage = _Storage()
    service = _FailingCommitService(storage)
    with pytest.raises(RuntimeError, match="db_commit_failed"):
        await service.submit(
            request=_request(),
            permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
            source=_Source(content),
            tool_call_id="call-db-failure",
            path="/workspace/outputs/chart.png",
            name="chart.png",
            media_type="image/png",
        )
    assert list(storage.values.values()) == [content]


@pytest.mark.asyncio
async def test_stale_sandbox_fence_stops_before_storage_write() -> None:
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    storage = _Storage()
    service = AgentArtifactSubmissionService(
        session_factory=lambda: None,
        run_repo_factory=lambda _: None,
        agent_repo_factory=lambda _: None,
        event_repo_factory=lambda _: None,
        storage=storage,
        execution_control=SimpleNamespace(assert_active=lambda _: None),
    )

    async def _active(_):
        return None

    service._execution_control = SimpleNamespace(assert_active=_active)
    with pytest.raises(AgentArtifactServiceError) as caught:
        await service.submit(
            request=_request(),
            permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
            source=_StaleSource(content),
            tool_call_id="call-stale",
            path="/workspace/outputs/chart.png",
            name="chart.png",
            media_type="image/png",
        )
    assert caught.value.code == "artifact_sandbox_fence_lost"
    assert storage.values == {}


@pytest.mark.asyncio
async def test_cancelled_runtime_stops_before_rejection_or_storage_side_effect() -> None:
    async def _cancelled(_):
        raise RuntimeExecutionControlError(
            "runtime_execution_lease_lost", "Runtime Execution lease 已失效"
        )

    storage = _Storage()
    service = AgentArtifactSubmissionService(
        session_factory=lambda: None,
        run_repo_factory=lambda _: None,
        agent_repo_factory=lambda _: None,
        event_repo_factory=lambda _: None,
        storage=storage,
        execution_control=SimpleNamespace(assert_active=_cancelled),
    )

    with pytest.raises(RuntimeExecutionControlError):
        await service.submit(
            request=_request(),
            permit=RuntimeExecutionPermit("turn-1", "owner-1", "attempt-1", 1),
            source=_Source(b"not-even-a-png"),
            tool_call_id="call-cancelled",
            path="/workspace/outputs/chart.png",
            name="chart.png",
            media_type="image/png",
        )

    assert storage.values == {}
