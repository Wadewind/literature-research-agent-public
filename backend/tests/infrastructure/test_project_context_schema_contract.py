"""Project Research Context 持久化 Schema 契约。"""

from literature_agent.infrastructure.persistence.models import (
    AgentMessageORM,
    AgentToolExecutionORM,
)


def test_agent_message_can_reference_claim_set_without_making_it_required() -> None:
    column = AgentMessageORM.__table__.c.claim_set_id
    assert column.nullable is True
    assert {fk.target_fullname for fk in column.foreign_keys} == {"claim_sets.claim_set_id"}


def test_tool_execution_has_stable_effect_uniqueness_and_safe_state_columns() -> None:
    constraints = {
        constraint.name for constraint in AgentToolExecutionORM.__table__.constraints
    }
    assert "uq_agent_tool_executions_turn_tool_args" in constraints
    assert "ck_agent_tool_error_kind" in constraints
    assert "ck_agent_tool_state_consistency" in constraints
    assert {
        "effect_id",
        "turn_run_id",
        "tool_name",
        "args_hash",
        "status",
        "result_payload",
        "result_hash",
        "error_kind",
        "error_code",
        "safe_message",
        "attempt_count",
    } <= set(AgentToolExecutionORM.__table__.c.keys())
