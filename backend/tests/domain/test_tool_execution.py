"""Project Tool effect 的稳定 ID、状态与安全结果边界。"""

import pytest

from literature_agent.domain.tool_execution import (
    ToolErrorKind,
    ToolExecutionStatus,
    canonical_tool_args,
    create_tool_execution,
)


def test_tool_effect_id_is_stable_for_canonical_argument_order() -> None:
    left = create_tool_execution(
        turn_run_id="turn-1",
        tool_name="search_project_chunks",
        arguments={"query": "graph", "limit": 4},
    )
    right = create_tool_execution(
        turn_run_id="turn-1",
        tool_name="search_project_chunks",
        arguments={"limit": 4, "query": "graph"},
    )

    assert canonical_tool_args({"query": "graph", "limit": 4}) == canonical_tool_args(
        {"limit": 4, "query": "graph"}
    )
    assert left.effect_id == right.effect_id
    assert left.args_hash == right.args_hash
    assert left.status is ToolExecutionStatus.RUNNING


def test_tool_execution_rejects_large_result_payload() -> None:
    execution = create_tool_execution(
        turn_run_id="turn-1",
        tool_name="read_review_evidence_matrix",
        arguments={},
    )

    with pytest.raises(ValueError, match="结果"):
        execution.succeed({"rows": [{"finding": "x" * 20_000}]})


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": object()},
        {"query": "x" * 4_001},
    ],
)
def test_tool_effect_rejects_non_object_non_finite_or_oversized_arguments(
    arguments,
) -> None:
    with pytest.raises(ValueError):
        create_tool_execution(
            turn_run_id="turn-1",
            tool_name="search_project_chunks",
            arguments=arguments,
        )


def test_tool_success_copies_mutable_payload_before_persisting_fact() -> None:
    execution = create_tool_execution(
        turn_run_id="turn-1",
        tool_name="search_project_chunks",
        arguments={"query": "graph"},
    )
    payload = {"items": [{"evidence_id": "e-1"}]}

    succeeded = execution.succeed(payload)
    payload["items"][0]["evidence_id"] = "mutated"

    assert succeeded.result_payload == {"items": [{"evidence_id": "e-1"}]}


def test_tool_execution_rejects_terminal_state_rewrites() -> None:
    running = create_tool_execution(
        turn_run_id="turn-1",
        tool_name="search_project_chunks",
        arguments={"query": "graph"},
    )
    succeeded = running.succeed({"items": []})
    failed = running.fail(ToolErrorKind.PERMANENT, "tool_denied", "Tool 不可用")

    with pytest.raises(ValueError, match="RUNNING"):
        succeeded.succeed({"items": []})
    with pytest.raises(ValueError, match="RUNNING"):
        succeeded.fail(ToolErrorKind.PERMANENT, "tool_denied", "Tool 不可用")
    with pytest.raises(ValueError, match="RUNNING"):
        failed.succeed({"items": []})
    with pytest.raises(ValueError, match="RUNNING"):
        failed.fail(ToolErrorKind.PERMANENT, "tool_denied", "Tool 不可用")


def test_tool_execution_rejects_values_longer_than_database_contract() -> None:
    with pytest.raises(ValueError, match="Tool effect"):
        create_tool_execution(
            turn_run_id="turn-1",
            tool_name="t" * 101,
            arguments={},
        )

    running = create_tool_execution(
        turn_run_id="turn-1",
        tool_name="search_project_chunks",
        arguments={},
    )
    with pytest.raises(ValueError, match="错误信息"):
        running.fail(ToolErrorKind.PERMANENT, "e" * 101, "Tool 不可用")
