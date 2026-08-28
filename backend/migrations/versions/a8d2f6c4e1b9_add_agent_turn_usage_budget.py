"""增加 Agent Turn 硬预算与脱敏 Tool 调用摘要。

Revision ID: a8d2f6c4e1b9
Revises: e2d7a9c4b1f6
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8d2f6c4e1b9"
down_revision: str | None = "e2d7a9c4b1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_policy_snapshots",
        sa.Column(
            "tool_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("agent_policy_snapshots", "tool_refs", server_default=None)
    for name, default in (
        ("wall_clock_limit_seconds", "300"),
        ("tool_timeout_seconds", "30"),
        ("execute_timeout_seconds", "60"),
        ("max_tool_output_bytes", "65536"),
        ("max_repeated_tool_calls", "2"),
        ("max_input_tokens_per_model_call", "60000"),
        ("max_output_tokens_per_model_call", "2048"),
    ):
        op.add_column(
            "agent_policy_snapshots",
            sa.Column(name, sa.Integer(), nullable=False, server_default=default),
        )
        op.alter_column("agent_policy_snapshots", name, server_default=None)
    op.create_check_constraint(
        "ck_agent_policy_positive_limits",
        "agent_policy_snapshots",
        "wall_clock_limit_seconds > 0 AND tool_timeout_seconds > 0 "
        "AND execute_timeout_seconds > 0 AND max_tool_output_bytes > 0 "
        "AND max_repeated_tool_calls > 0 "
        "AND max_input_tokens_per_model_call > 0 "
        "AND max_output_tokens_per_model_call > 0",
    )
    op.create_table(
        "agent_turn_usages",
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            primary_key=True,
        ),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot_id",
            sa.String(36),
            sa.ForeignKey("agent_policy_snapshots.snapshot_id"),
            nullable=False,
        ),
        sa.Column("max_model_calls", sa.Integer(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("wall_clock_limit_seconds", sa.Integer(), nullable=False),
        sa.Column("tool_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("execute_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_tool_output_bytes", sa.Integer(), nullable=False),
        sa.Column("max_repeated_tool_calls", sa.Integer(), nullable=False),
        sa.Column("max_input_tokens_per_model_call", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens_per_model_call", sa.Integer(), nullable=False),
        sa.Column("model_calls_reserved", sa.Integer(), nullable=False),
        sa.Column("tool_calls_reserved", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "max_model_calls >= 0 AND model_calls_reserved BETWEEN 0 AND max_model_calls",
            name="ck_agent_usage_model_calls",
        ),
        sa.CheckConstraint(
            "max_tool_calls >= 0 AND tool_calls_reserved BETWEEN 0 AND max_tool_calls",
            name="ck_agent_usage_tool_calls",
        ),
        sa.CheckConstraint(
            "wall_clock_limit_seconds > 0 AND tool_timeout_seconds > 0 "
            "AND execute_timeout_seconds > 0 AND max_tool_output_bytes > 0 "
            "AND max_repeated_tool_calls > 0 "
            "AND max_input_tokens_per_model_call > 0 "
            "AND max_output_tokens_per_model_call > 0",
            name="ck_agent_usage_positive_limits",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_agent_usage_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_agent_usage_output_tokens",
        ),
        sa.CheckConstraint(
            "(started_at IS NULL AND deadline_at IS NULL) OR "
            "(started_at IS NOT NULL AND deadline_at > started_at)",
            name="ck_agent_usage_deadline",
        ),
    )
    op.create_index("ix_agent_turn_usages_owner_id", "agent_turn_usages", ["owner_id"])

    op.create_table(
        "agent_model_call_reservations",
        sa.Column("reservation_key", sa.String(255), primary_key=True),
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_usages.turn_run_id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ordinal >= 1", name="ck_agent_model_call_ordinal"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_agent_model_call_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_agent_model_call_output_tokens",
        ),
        sa.UniqueConstraint("turn_run_id", "ordinal", name="uq_agent_model_calls_turn_ordinal"),
    )
    op.create_index(
        "ix_agent_model_call_reservations_turn_run_id",
        "agent_model_call_reservations",
        ["turn_run_id"],
    )

    op.create_table(
        "agent_tool_calls",
        sa.Column("reservation_key", sa.String(255), primary_key=True),
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_usages.turn_run_id"),
            nullable=False,
        ),
        sa.Column("invocation_id", sa.String(255), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("tool_version", sa.String(100), nullable=False),
        sa.Column("input_schema_hash", sa.String(64), nullable=False),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_size_bytes", sa.Integer(), nullable=False),
        sa.Column("output_size_bytes", sa.Integer()),
        sa.Column("result_hash", sa.String(64)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("safe_message", sa.String(500)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('reserved','running','succeeded','failed')",
            name="ck_agent_tool_call_status",
        ),
        sa.CheckConstraint(
            "input_size_bytes >= 0 AND "
            "(output_size_bytes IS NULL OR output_size_bytes BETWEEN 0 AND 65536)",
            name="ck_agent_tool_call_sizes",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_agent_tool_call_duration",
        ),
        sa.CheckConstraint(
            "(status = 'reserved' AND started_at IS NULL AND completed_at IS NULL "
            "AND output_size_bytes IS NULL AND result_hash IS NULL "
            "AND error_code IS NULL AND safe_message IS NULL AND duration_ms IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND output_size_bytes IS NULL AND result_hash IS NULL "
            "AND error_code IS NULL AND safe_message IS NULL AND duration_ms IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND output_size_bytes IS NOT NULL AND result_hash IS NOT NULL "
            "AND error_code IS NULL AND safe_message IS NULL AND duration_ms IS NOT NULL) OR "
            "(status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND output_size_bytes IS NULL AND result_hash IS NULL "
            "AND error_code IS NOT NULL AND safe_message IS NOT NULL "
            "AND duration_ms IS NOT NULL)",
            name="ck_agent_tool_call_state_fields",
        ),
        sa.UniqueConstraint(
            "turn_run_id", "invocation_id", name="uq_agent_tool_calls_turn_invocation"
        ),
    )
    op.create_index("ix_agent_tool_calls_turn_run_id", "agent_tool_calls", ["turn_run_id"])
    op.create_index(
        "ix_agent_tool_calls_turn_name_args",
        "agent_tool_calls",
        ["turn_run_id", "tool_name", "args_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_tool_calls_turn_name_args", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_turn_run_id", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index(
        "ix_agent_model_call_reservations_turn_run_id",
        table_name="agent_model_call_reservations",
    )
    op.drop_table("agent_model_call_reservations")
    op.drop_index("ix_agent_turn_usages_owner_id", table_name="agent_turn_usages")
    op.drop_table("agent_turn_usages")
    op.drop_constraint("ck_agent_policy_positive_limits", "agent_policy_snapshots", type_="check")
    for name in (
        "max_output_tokens_per_model_call",
        "max_input_tokens_per_model_call",
        "max_repeated_tool_calls",
        "max_tool_output_bytes",
        "execute_timeout_seconds",
        "tool_timeout_seconds",
        "wall_clock_limit_seconds",
    ):
        op.drop_column("agent_policy_snapshots", name)
    op.drop_column("agent_policy_snapshots", "tool_refs")
