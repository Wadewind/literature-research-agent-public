"""增加 Agent Sandbox Lease 与 WorkspaceSnapshot。

Revision ID: d1f6a8c3e2b4
Revises: a4c8e1f2b7d9
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f6a8c3e2b4"
down_revision: str | None = "a4c8e1f2b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_sandbox_leases",
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_sessions.session_id"),
            primary_key=True,
        ),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.project_id"), nullable=False
        ),
        sa.Column(
            "holder_turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            nullable=False,
        ),
        sa.Column("sandbox_id", sa.String(255), nullable=False, unique=True),
        sa.Column("image_ref", sa.String(500), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation >= 1", name="ck_agent_sandbox_lease_generation"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_agent_sandbox_lease_fence"),
        sa.CheckConstraint(
            "status IN ('active','dirty')", name="ck_agent_sandbox_lease_status"
        ),
    )
    op.create_index(
        "ix_agent_sandbox_leases_project_id", "agent_sandbox_leases", ["project_id"]
    )
    op.create_index(
        "ix_agent_sandbox_leases_expires_at", "agent_sandbox_leases", ["expires_at"]
    )
    op.create_table(
        "agent_workspace_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.project_id"), nullable=False
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column(
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sandbox_generation", sa.Integer(), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column("total_size_bytes", sa.Integer(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_agent_workspace_snapshot_version"),
        sa.CheckConstraint(
            "sandbox_generation >= 1",
            name="ck_agent_workspace_snapshot_generation",
        ),
        sa.CheckConstraint(
            "total_size_bytes BETWEEN 0 AND 52428800",
            name="ck_agent_workspace_snapshot_total_size",
        ),
        sa.CheckConstraint(
            "status IN ('staged','stable')",
            name="ck_agent_workspace_snapshot_status",
        ),
    )
    op.create_index(
        "ix_agent_workspace_snapshots_project_id",
        "agent_workspace_snapshots",
        ["project_id"],
    )
    op.create_index(
        "ix_agent_workspace_snapshots_session_id",
        "agent_workspace_snapshots",
        ["session_id"],
    )
    op.create_index(
        "ix_agent_workspace_snapshots_status",
        "agent_workspace_snapshots",
        ["status"],
    )
    op.create_index(
        "uq_agent_workspace_snapshot_stable_session_version",
        "agent_workspace_snapshots",
        ["session_id", "version"],
        unique=True,
        postgresql_where=sa.text("status = 'stable'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_workspace_snapshots_session_id",
        table_name="agent_workspace_snapshots",
    )
    op.drop_index(
        "ix_agent_workspace_snapshots_project_id",
        table_name="agent_workspace_snapshots",
    )
    op.drop_table("agent_workspace_snapshots")
    op.drop_index(
        "ix_agent_sandbox_leases_expires_at", table_name="agent_sandbox_leases"
    )
    op.drop_index(
        "ix_agent_sandbox_leases_project_id", table_name="agent_sandbox_leases"
    )
    op.drop_table("agent_sandbox_leases")
