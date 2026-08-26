from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.domain.runtime_execution import (
    RuntimeControlState,
    RuntimeExecutionError,
    create_runtime_execution,
)

_NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)


def _execution():
    return create_runtime_execution(
        turn_run_id="turn-1",
        session_id="session-1",
        runtime_execution_id="execution-1",
        request_hash="a" * 64,
        runtime_revision="research-agent-runtime.v1",
        graph_revision="deep-agent-graph.v1",
        deepagents_version="0.7.8",
        langgraph_version="1.2.11",
        attempt_id="attempt-1",
        lease_owner_id="worker-a",
        lease_expires_at=_NOW + timedelta(seconds=30),
        now=_NOW,
    )


def test_runtime_execution_reclaim_increments_fence_and_rejects_live_lease() -> None:
    execution = _execution()
    assert execution.fencing_token == 1
    assert not execution.is_orphaned(_NOW + timedelta(seconds=10))

    with pytest.raises(RuntimeExecutionError, match="仍有有效 owner"):
        execution.claim(
            attempt_id="attempt-2",
            lease_owner_id="worker-b",
            lease_expires_at=_NOW + timedelta(seconds=40),
            now=_NOW + timedelta(seconds=10),
        )

    reclaimed = execution.claim(
        attempt_id="attempt-2",
        lease_owner_id="worker-b",
        lease_expires_at=_NOW + timedelta(seconds=70),
        now=_NOW + timedelta(seconds=31),
    )
    assert reclaimed.fencing_token == 2
    assert reclaimed.current_attempt_id == "attempt-2"
    assert reclaimed.lease_owner_id == "worker-b"


def test_runtime_execution_stale_owner_cannot_checkpoint_or_finish() -> None:
    reclaimed = _execution().claim(
        attempt_id="attempt-2",
        lease_owner_id="worker-b",
        lease_expires_at=_NOW + timedelta(seconds=70),
        now=_NOW + timedelta(seconds=31),
    )

    with pytest.raises(RuntimeExecutionError, match="fencing"):
        reclaimed.record_checkpoint(
            owner_id="worker-a",
            attempt_id="attempt-1",
            fencing_token=1,
            checkpoint_id="checkpoint-stale",
            now=_NOW + timedelta(seconds=32),
        )
    with pytest.raises(RuntimeExecutionError, match="fencing"):
        reclaimed.succeed(
            owner_id="worker-a",
            attempt_id="attempt-1",
            fencing_token=1,
            checkpoint_id="checkpoint-stale",
            now=_NOW + timedelta(seconds=32),
        )


def test_runtime_execution_temporary_error_remains_recoverable_and_terminal_is_immutable() -> None:
    execution = _execution().record_temporary_error(
        owner_id="worker-a",
        attempt_id="attempt-1",
        fencing_token=1,
        error_code="provider_unavailable",
        safe_message="Provider 暂时不可用",
        now=_NOW + timedelta(seconds=5),
    )
    assert execution.state is RuntimeControlState.RUNNING
    assert execution.lease_owner_id is None
    assert execution.is_orphaned(_NOW + timedelta(seconds=6))

    reclaimed = execution.claim(
        attempt_id="attempt-2",
        lease_owner_id="worker-b",
        lease_expires_at=_NOW + timedelta(seconds=60),
        now=_NOW + timedelta(seconds=6),
    )
    succeeded = reclaimed.succeed(
        owner_id="worker-b",
        attempt_id="attempt-2",
        fencing_token=2,
        checkpoint_id="checkpoint-final",
        now=_NOW + timedelta(seconds=10),
    )
    assert succeeded.state is RuntimeControlState.SUCCEEDED
    with pytest.raises(RuntimeExecutionError, match="终态"):
        succeeded.cancel(now=_NOW + timedelta(seconds=11))


def test_runtime_execution_revision_mismatch_fails_closed() -> None:
    execution = _execution()
    with pytest.raises(RuntimeExecutionError, match="版本不兼容"):
        execution.require_compatible(
            session_id="session-1",
            runtime_execution_id="execution-1",
            request_hash="a" * 64,
            runtime_revision="research-agent-runtime.v2",
            graph_revision="deep-agent-graph.v1",
            deepagents_version="0.7.8",
            langgraph_version="1.2.11",
        )


@pytest.mark.parametrize(
    ("session_id", "runtime_execution_id"),
    [("session-other", "execution-1"), ("session-1", "execution-other")],
)
def test_runtime_execution_identity_mismatch_fails_closed(
    session_id: str, runtime_execution_id: str
) -> None:
    with pytest.raises(RuntimeExecutionError, match="身份冲突"):
        _execution().require_compatible(
            session_id=session_id,
            runtime_execution_id=runtime_execution_id,
            request_hash="a" * 64,
            runtime_revision="research-agent-runtime.v1",
            graph_revision="deep-agent-graph.v1",
            deepagents_version="0.7.8",
            langgraph_version="1.2.11",
        )
