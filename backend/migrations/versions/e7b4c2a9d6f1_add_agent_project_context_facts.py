"""增加 Agent Project Context Tool 与引用结果事实。

Revision ID: e7b4c2a9d6f1
Revises: c1e5a7d9b3f2
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7b4c2a9d6f1"
down_revision: str | None = "c1e5a7d9b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_messages", sa.Column("claim_set_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_agent_messages_claim_set",
        "agent_messages",
        "claim_sets",
        ["claim_set_id"],
        ["claim_set_id"],
    )
    op.create_table(
        "agent_tool_executions",
        sa.Column("effect_id", sa.String(255), primary_key=True),
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("error_kind", sa.String(20), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("safe_message", sa.String(500), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed')", name="ck_agent_tool_status"
        ),
        sa.CheckConstraint(
            "error_kind IS NULL OR error_kind IN ('temporary','permanent','cancelled')",
            name="ck_agent_tool_error_kind",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND result_payload IS NULL AND result_hash IS NULL "
            "AND error_kind IS NULL AND error_code IS NULL AND safe_message IS NULL) OR "
            "(status = 'succeeded' AND result_payload IS NOT NULL "
            "AND result_hash IS NOT NULL AND error_kind IS NULL "
            "AND error_code IS NULL AND safe_message IS NULL) OR "
            "(status = 'failed' AND result_payload IS NULL AND result_hash IS NULL "
            "AND error_kind IS NOT NULL AND error_code IS NOT NULL "
            "AND safe_message IS NOT NULL)",
            name="ck_agent_tool_state_consistency",
        ),
        sa.CheckConstraint("attempt_count >= 1", name="ck_agent_tool_attempt_count"),
        sa.UniqueConstraint(
            "turn_run_id",
            "tool_name",
            "args_hash",
            name="uq_agent_tool_executions_turn_tool_args",
        ),
    )
    op.create_index(
        "ix_agent_tool_executions_turn_run_id",
        "agent_tool_executions",
        ["turn_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_tool_executions_turn_run_id", table_name="agent_tool_executions"
    )
    op.drop_table("agent_tool_executions")
    op.drop_constraint("fk_agent_messages_claim_set", "agent_messages", type_="foreignkey")
    op.drop_column("agent_messages", "claim_set_id")
