"""创建 AgentSession/Turn 离线业务闭环表

Revision ID: c1e5a7d9b3f2
Revises: b9d4e7f1a2c6
Create Date: 2026-08-25 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1e5a7d9b3f2"
down_revision: str | Sequence[str] | None = "b9d4e7f1a2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.project_id"), nullable=False
        ),
        sa.Column("title", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active_turn_run_id", sa.String(36)),
        sa.Column("next_message_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','closed')", name="ck_agent_sessions_status"),
    )
    op.create_index("ix_agent_sessions_owner_id", "agent_sessions", ["owner_id"])
    op.create_index("ix_agent_sessions_project_id", "agent_sessions", ["project_id"])
    op.create_table(
        "agent_messages",
        sa.Column("message_id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("agent_sessions.session_id"), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("turn_run_id", sa.String(36), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_messages_sequence"),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_agent_messages_role"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_agent_messages_session_sequence"),
        sa.UniqueConstraint(
            "session_id", "idempotency_key", name="uq_agent_messages_session_idempotency"
        ),
    )
    op.create_index("ix_agent_messages_session_id", "agent_messages", ["session_id"])
    op.create_index("ix_agent_messages_turn_run_id", "agent_messages", ["turn_run_id"])
    op.create_table(
        "agent_context_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("agent_sessions.session_id"), nullable=False
        ),
        sa.Column(
            "turn_run_id", sa.String(36), sa.ForeignKey("runs.run_id"), unique=True, nullable=False
        ),
        sa.Column(
            "user_message_id",
            sa.String(36),
            nullable=False,
        ),
        sa.Column("history_through_sequence", sa.Integer(), nullable=False),
        sa.Column("project_index_refs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "review_output_id",
            sa.String(36),
            sa.ForeignKey("review_outputs.output_id"),
            nullable=False,
        ),
        sa.Column("artifact_refs", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_agent_context_snapshots_session_id", "agent_context_snapshots", ["session_id"]
    )
    op.create_table(
        "agent_policy_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("agent_sessions.session_id"), nullable=False
        ),
        sa.Column(
            "turn_run_id", sa.String(36), sa.ForeignKey("runs.run_id"), unique=True, nullable=False
        ),
        sa.Column("allowed_tool_names", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_skill_names", postgresql.JSONB(), nullable=False),
        sa.Column("network_enabled", sa.Boolean(), nullable=False),
        sa.Column("sandbox_enabled", sa.Boolean(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("max_model_calls", sa.Integer(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_agent_policy_snapshots_session_id", "agent_policy_snapshots", ["session_id"]
    )
    op.create_table(
        "agent_turn_runs",
        sa.Column("turn_run_id", sa.String(36), sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("agent_sessions.session_id"), nullable=False
        ),
        sa.Column(
            "user_message_id",
            sa.String(36),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "context_snapshot_id",
            sa.String(36),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot_id",
            sa.String(36),
            unique=True,
            nullable=False,
        ),
    )
    op.create_index("ix_agent_turn_runs_session_id", "agent_turn_runs", ["session_id"])
    op.create_foreign_key(
        "fk_agent_context_snapshots_user_message",
        "agent_context_snapshots",
        "agent_messages",
        ["user_message_id"],
        ["message_id"],
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_agent_turn_runs_user_message",
        "agent_turn_runs",
        "agent_messages",
        ["user_message_id"],
        ["message_id"],
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_agent_turn_runs_context_snapshot",
        "agent_turn_runs",
        "agent_context_snapshots",
        ["context_snapshot_id"],
        ["snapshot_id"],
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_agent_turn_runs_policy_snapshot",
        "agent_turn_runs",
        "agent_policy_snapshots",
        ["policy_snapshot_id"],
        ["snapshot_id"],
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_agent_sessions_active_turn",
        "agent_sessions",
        "agent_turn_runs",
        ["active_turn_run_id"],
        ["turn_run_id"],
        use_alter=True,
    )
    op.create_table(
        "agent_runtime_session_bindings",
        sa.Column("binding_id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("agent_sessions.session_id"), nullable=False
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("runtime_thread_id", sa.String(255), unique=True, nullable=False),
        sa.Column("runtime_workspace_id", sa.String(255), nullable=False),
        sa.CheckConstraint("generation >= 1", name="ck_agent_runtime_session_generation"),
        sa.UniqueConstraint("session_id", "generation", name="uq_agent_runtime_session_generation"),
    )
    op.create_index(
        "ix_agent_runtime_session_bindings_session_id",
        "agent_runtime_session_bindings",
        ["session_id"],
    )
    op.create_table(
        "agent_runtime_turn_bindings",
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            primary_key=True,
        ),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("agent_sessions.session_id"), nullable=False
        ),
        sa.Column(
            "session_binding_id",
            sa.String(255),
            sa.ForeignKey("agent_runtime_session_bindings.binding_id"),
            nullable=False,
        ),
        sa.Column("runtime_execution_id", sa.String(255), unique=True, nullable=False),
        sa.Column("runtime_checkpoint_id", sa.String(255), nullable=False),
    )
    op.create_index(
        "ix_agent_runtime_turn_bindings_session_id", "agent_runtime_turn_bindings", ["session_id"]
    )
    op.create_table(
        "agent_artifact_candidates",
        sa.Column("candidate_id", sa.String(255), primary_key=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.project_id"), nullable=False
        ),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("agent_sessions.session_id"), nullable=False
        ),
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("content_ref", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes BETWEEN 0 AND 1000000", name="ck_agent_candidate_size"),
        sa.CheckConstraint("status = 'staged'", name="ck_agent_candidate_status"),
        sa.UniqueConstraint("turn_run_id", "content_hash", name="uq_agent_candidate_turn_hash"),
    )
    op.create_index(
        "ix_agent_artifact_candidates_owner_id", "agent_artifact_candidates", ["owner_id"]
    )
    op.create_index(
        "ix_agent_artifact_candidates_project_id", "agent_artifact_candidates", ["project_id"]
    )
    op.create_index(
        "ix_agent_artifact_candidates_session_id", "agent_artifact_candidates", ["session_id"]
    )
    op.create_index(
        "ix_agent_artifact_candidates_turn_run_id", "agent_artifact_candidates", ["turn_run_id"]
    )


def downgrade() -> None:
    op.drop_table("agent_artifact_candidates")
    op.drop_table("agent_runtime_turn_bindings")
    op.drop_table("agent_runtime_session_bindings")
    op.drop_constraint("fk_agent_sessions_active_turn", "agent_sessions", type_="foreignkey")
    op.drop_table("agent_turn_runs")
    op.drop_table("agent_policy_snapshots")
    op.drop_table("agent_context_snapshots")
    op.drop_table("agent_messages")
    op.drop_table("agent_sessions")
