"""增加 Agent Runtime Execution lease 与 fencing 控制事实。

Revision ID: a4c8e1f2b7d9
Revises: e7b4c2a9d6f1
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c8e1f2b7d9"
down_revision: str | None = "e7b4c2a9d6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runtime_executions",
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("runtime_execution_id", sa.String(255), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("runtime_revision", sa.String(100), nullable=False),
        sa.Column("graph_revision", sa.String(100), nullable=False),
        sa.Column("deepagents_version", sa.String(50), nullable=False),
        sa.Column("langgraph_version", sa.String(50), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column(
            "current_attempt_id",
            sa.String(36),
            sa.ForeignKey("run_attempts.attempt_id"),
            nullable=True,
        ),
        sa.Column("lease_owner_id", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checkpoint_id", sa.String(255), nullable=True),
        sa.Column("last_error_kind", sa.String(20), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_safe_message", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('running','interrupted','succeeded','failed','cancelled')",
            name="ck_agent_runtime_execution_state",
        ),
        sa.CheckConstraint("fencing_token >= 1", name="ck_agent_runtime_execution_fence"),
        sa.CheckConstraint(
            "(state = 'running' AND finished_at IS NULL) OR "
            "(state <> 'running' AND finished_at IS NOT NULL)",
            name="ck_agent_runtime_execution_finished",
        ),
        sa.CheckConstraint(
            "(current_attempt_id IS NULL AND lease_owner_id IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(current_attempt_id IS NOT NULL AND lease_owner_id IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_agent_runtime_execution_lease",
        ),
        sa.CheckConstraint(
            "last_error_kind IS NULL OR last_error_kind IN "
            "('temporary','permanent','cancelled')",
            name="ck_agent_runtime_execution_error_kind",
        ),
    )
    op.create_index(
        "ix_agent_runtime_executions_session_id",
        "agent_runtime_executions",
        ["session_id"],
    )
    op.create_index(
        "ix_agent_runtime_executions_state",
        "agent_runtime_executions",
        ["state"],
    )
    op.create_index(
        "ix_agent_runtime_executions_current_attempt_id",
        "agent_runtime_executions",
        ["current_attempt_id"],
    )
    op.create_index(
        "ix_agent_runtime_executions_lease_expires_at",
        "agent_runtime_executions",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_runtime_executions_lease_expires_at",
        table_name="agent_runtime_executions",
    )
    op.drop_index(
        "ix_agent_runtime_executions_current_attempt_id",
        table_name="agent_runtime_executions",
    )
    op.drop_index(
        "ix_agent_runtime_executions_state",
        table_name="agent_runtime_executions",
    )
    op.drop_index(
        "ix_agent_runtime_executions_session_id",
        table_name="agent_runtime_executions",
    )
    op.drop_table("agent_runtime_executions")
