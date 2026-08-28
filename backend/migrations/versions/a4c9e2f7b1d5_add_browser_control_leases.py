"""增加 BrowserControlLease 业务事实。

Revision ID: a4c9e2f7b1d5
Revises: f3a6c8d1e2b4
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c9e2f7b1d5"
down_revision: str | None = "f3a6c8d1e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_browser_control_leases",
        sa.Column("control_id", sa.String(36), primary_key=True),
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
            "anchor_turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            nullable=False,
        ),
        sa.Column("sandbox_generation", sa.Integer(), nullable=False),
        sa.Column("sandbox_fencing_token", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("ticket_digest", sa.String(64), nullable=False),
        sa.Column("viewer_connection_id", sa.String(36)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(100)),
        sa.UniqueConstraint("ticket_digest", name="uq_agent_browser_control_ticket_digest"),
        sa.UniqueConstraint(
            "session_id", "revision", name="uq_agent_browser_control_session_revision"
        ),
        sa.CheckConstraint("sandbox_generation >= 1", name="ck_browser_control_generation"),
        sa.CheckConstraint(
            "sandbox_fencing_token >= 1", name="ck_browser_control_fence"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_browser_control_revision"),
        sa.CheckConstraint("mode = 'manual'", name="ck_browser_control_mode"),
        sa.CheckConstraint(
            "status IN ('active','ended','expired')", name="ck_browser_control_status"
        ),
        sa.CheckConstraint(
            "started_at < expires_at AND "
            "expires_at <= started_at + interval '5 minutes'",
            name="ck_browser_control_ttl",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND ended_at IS NULL AND end_reason IS NULL) OR "
            "(status IN ('ended','expired') AND ended_at IS NOT NULL "
            "AND end_reason IS NOT NULL AND viewer_connection_id IS NULL)",
            name="ck_browser_control_state_fields",
        ),
    )
    op.create_index(
        "ix_agent_browser_control_leases_owner_id",
        "agent_browser_control_leases",
        ["owner_id"],
    )
    op.create_index(
        "ix_agent_browser_control_leases_project_id",
        "agent_browser_control_leases",
        ["project_id"],
    )
    op.create_index(
        "ix_agent_browser_control_leases_session_id",
        "agent_browser_control_leases",
        ["session_id"],
    )
    op.create_index(
        "ix_agent_browser_control_leases_status",
        "agent_browser_control_leases",
        ["status"],
    )
    op.create_index(
        "ix_agent_browser_control_leases_expires_at",
        "agent_browser_control_leases",
        ["expires_at"],
    )
    op.create_index(
        "uq_agent_browser_control_active_session",
        "agent_browser_control_leases",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_browser_control_active_session",
        table_name="agent_browser_control_leases",
    )
    op.drop_index(
        "ix_agent_browser_control_leases_expires_at",
        table_name="agent_browser_control_leases",
    )
    op.drop_index(
        "ix_agent_browser_control_leases_status",
        table_name="agent_browser_control_leases",
    )
    op.drop_index(
        "ix_agent_browser_control_leases_session_id",
        table_name="agent_browser_control_leases",
    )
    op.drop_index(
        "ix_agent_browser_control_leases_project_id",
        table_name="agent_browser_control_leases",
    )
    op.drop_index(
        "ix_agent_browser_control_leases_owner_id",
        table_name="agent_browser_control_leases",
    )
    op.drop_table("agent_browser_control_leases")
