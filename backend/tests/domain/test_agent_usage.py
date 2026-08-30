"""Agent Turn 硬预算与安全 Tool 调用摘要的领域契约。"""

from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.domain.agent_usage import (
    AGENT_EXECUTE_TIMEOUT_SECONDS,
    AGENT_MAX_INPUT_TOKENS_PER_MODEL_CALL,
    AGENT_MAX_OUTPUT_TOKENS_PER_MODEL_CALL,
    AGENT_MAX_REPEATED_TOOL_CALLS,
    AGENT_MAX_TOOL_OUTPUT_BYTES,
    AGENT_TOOL_TIMEOUT_SECONDS,
    AGENT_TURN_WALL_CLOCK_SECONDS,
    AgentToolCall,
    AgentToolCallStatus,
    AgentTurnUsage,
    create_agent_model_call_reservation,
    create_agent_tool_call,
    create_agent_turn_usage,
)

_NOW = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


def test_turn_usage_freezes_lean_profile_and_starts_deadline_once() -> None:
    usage = create_agent_turn_usage(
        turn_run_id="turn-1",
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        policy_snapshot_id="policy-1",
        max_model_calls=8,
        max_tool_calls=12,
        now=_NOW,
    )

    assert usage.started_at is None
    assert usage.deadline_at is None
    assert usage.wall_clock_limit_seconds == AGENT_TURN_WALL_CLOCK_SECONDS == 300
    assert usage.tool_timeout_seconds == AGENT_TOOL_TIMEOUT_SECONDS == 60
    assert usage.execute_timeout_seconds == AGENT_EXECUTE_TIMEOUT_SECONDS == 60
    assert usage.max_tool_output_bytes == AGENT_MAX_TOOL_OUTPUT_BYTES == 64 * 1024
    assert usage.max_repeated_tool_calls == AGENT_MAX_REPEATED_TOOL_CALLS == 2
    assert usage.max_input_tokens_per_model_call == AGENT_MAX_INPUT_TOKENS_PER_MODEL_CALL == 60_000
    assert usage.max_output_tokens_per_model_call == AGENT_MAX_OUTPUT_TOKENS_PER_MODEL_CALL == 4_096

    started = usage.start(now=_NOW + timedelta(seconds=10))
    replayed = started.start(now=_NOW + timedelta(seconds=20))

    assert started.started_at == _NOW + timedelta(seconds=10)
    assert started.deadline_at == _NOW + timedelta(seconds=310)
    assert replayed == started


def test_tool_call_keeps_hashes_and_bounded_safe_previews() -> None:
    call = create_agent_tool_call(
        turn_run_id="turn-1",
        invocation_id="call-1",
        tool_name="execute",
        tool_version="deepagents-0.7.8",
        input_schema_hash="a" * 64,
        args_hash="b" * 64,
        input_size_bytes=42,
        input_preview='{"command":"git status --short"}',
        input_preview_truncated=False,
        now=_NOW,
    )

    assert call.status is AgentToolCallStatus.RESERVED
    assert not hasattr(call, "arguments")
    assert not hasattr(call, "result_payload")

    running = call.start(now=_NOW + timedelta(milliseconds=5))
    succeeded = running.succeed(
        output_size_bytes=512,
        result_hash="c" * 64,
        output_preview="无输出",
        output_preview_truncated=False,
        now=_NOW + timedelta(milliseconds=25),
    )

    assert succeeded.status is AgentToolCallStatus.SUCCEEDED
    assert succeeded.duration_ms == 20
    assert succeeded.output_size_bytes == 512
    assert succeeded.result_hash == "c" * 64
    assert succeeded.input_preview == '{"command":"git status --short"}'
    assert succeeded.output_preview == "无输出"
    assert succeeded.error_code is None


def test_tool_call_rejects_oversized_output_and_unsafe_error() -> None:
    call = create_agent_tool_call(
        turn_run_id="turn-1",
        invocation_id="call-1",
        tool_name="search_project_chunks",
        tool_version="1.0.0",
        input_schema_hash="a" * 64,
        args_hash="b" * 64,
        input_size_bytes=1,
        now=_NOW,
    ).start(now=_NOW)

    with pytest.raises(ValueError, match="输出"):
        call.succeed(
            output_size_bytes=AGENT_MAX_TOOL_OUTPUT_BYTES + 1,
            result_hash="c" * 64,
            now=_NOW,
        )
    with pytest.raises(ValueError, match="错误"):
        call.fail(
            error_code="",
            safe_message="secret=raw",
            now=_NOW,
        )


def test_domain_facts_reject_invalid_scope_hashes_and_limits() -> None:
    with pytest.raises(ValueError):
        AgentTurnUsage(
            turn_run_id="",
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            policy_snapshot_id="policy-1",
            max_model_calls=8,
            max_tool_calls=12,
            wall_clock_limit_seconds=300,
            tool_timeout_seconds=30,
            execute_timeout_seconds=60,
            max_tool_output_bytes=64 * 1024,
            max_repeated_tool_calls=2,
            max_input_tokens_per_model_call=60_000,
            max_output_tokens_per_model_call=2_048,
            model_calls_reserved=0,
            tool_calls_reserved=0,
            input_tokens=None,
            output_tokens=None,
            started_at=None,
            deadline_at=None,
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_model_usage_allows_two_phase_fill_and_rejects_conflicting_replay() -> None:
    reserved = create_agent_model_call_reservation(turn_run_id="turn-1", ordinal=1, now=_NOW)
    with_input = reserved.record_tokens(
        input_tokens=120, output_tokens=None, now=_NOW + timedelta(seconds=1)
    )
    completed = with_input.record_tokens(
        input_tokens=None, output_tokens=30, now=_NOW + timedelta(seconds=2)
    )

    assert completed.input_tokens == 120
    assert completed.output_tokens == 30
    assert completed.record_tokens(input_tokens=120, output_tokens=30) == completed
    with pytest.raises(ValueError, match="input Token"):
        completed.record_tokens(input_tokens=121, output_tokens=30)
    with pytest.raises(ValueError, match="output Token"):
        completed.record_tokens(input_tokens=120, output_tokens=31)
    with pytest.raises(ValueError):
        AgentToolCall(
            reservation_key="tool:turn-1:call-1",
            turn_run_id="turn-1",
            invocation_id="call-1",
            tool_name="execute",
            tool_version="1.0.0",
            input_schema_hash="not-a-hash",
            args_hash="b" * 64,
            status=AgentToolCallStatus.RESERVED,
            input_size_bytes=0,
            output_size_bytes=None,
            result_hash=None,
            error_code=None,
            safe_message=None,
            duration_ms=None,
            started_at=None,
            completed_at=None,
            created_at=_NOW,
            updated_at=_NOW,
        )
