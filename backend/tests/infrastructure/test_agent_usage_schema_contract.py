"""Agent Usage/Tool 调用摘要的数据库结构契约。"""

from literature_agent.infrastructure.persistence.models import (
    AgentPolicySnapshotORM,
    AgentToolCallORM,
    AgentTurnUsageORM,
)


def test_usage_and_tool_call_tables_keep_raw_payload_out_of_schema() -> None:
    policy = AgentPolicySnapshotORM.__table__
    usage = AgentTurnUsageORM.__table__
    calls = AgentToolCallORM.__table__

    assert usage.primary_key.columns.keys() == ["turn_run_id"]
    assert {
        "owner_id",
        "project_id",
        "session_id",
        "policy_snapshot_id",
        "started_at",
        "deadline_at",
        "model_calls_reserved",
        "tool_calls_reserved",
    }.issubset(usage.columns.keys())
    fixed_limits = {
        "wall_clock_limit_seconds",
        "tool_timeout_seconds",
        "execute_timeout_seconds",
        "max_tool_output_bytes",
        "max_repeated_tool_calls",
        "max_input_tokens_per_model_call",
        "max_output_tokens_per_model_call",
    }
    assert fixed_limits.issubset(policy.columns.keys())
    assert fixed_limits.issubset(usage.columns.keys())
    assert calls.primary_key.columns.keys() == ["reservation_key"]
    assert {
        "invocation_id",
        "tool_name",
        "tool_version",
        "input_schema_hash",
        "args_hash",
        "input_size_bytes",
        "input_preview",
        "input_preview_truncated",
        "output_size_bytes",
        "output_preview",
        "output_preview_truncated",
        "result_hash",
        "error_code",
        "safe_message",
        "duration_ms",
    }.issubset(calls.columns.keys())
    assert "arguments" not in calls.columns
    assert "result_payload" not in calls.columns
    assert "endpoint" not in calls.columns


def test_tool_call_identity_and_repeat_query_are_indexed() -> None:
    calls = AgentToolCallORM.__table__
    unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in calls.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    index_sets = {tuple(index.columns.keys()) for index in calls.indexes}

    assert ("turn_run_id", "invocation_id") in unique_sets
    assert ("turn_run_id", "tool_name", "args_hash") in index_sets
