"""增加 Agent MCP Profile 与 Policy MCP 引用。

Revision ID: f6a2c9d4e7b1
Revises: d1f6a8c3e2b4
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a2c9d4e7b1"
down_revision: str | None = "d1f6a8c3e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_policy_snapshots",
        sa.Column(
            "mcp_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("agent_policy_snapshots", "mcp_refs", server_default=None)
    op.create_table(
        "agent_mcp_profiles",
        sa.Column("profile_id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("selections", postgresql.JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_agent_mcp_profiles_revision"),
        sa.UniqueConstraint(
            "session_id", "revision", name="uq_agent_mcp_profiles_session_revision"
        ),
    )
    op.create_index("ix_agent_mcp_profiles_owner_id", "agent_mcp_profiles", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_mcp_profiles_owner_id", table_name="agent_mcp_profiles")
    op.drop_table("agent_mcp_profiles")
    op.drop_column("agent_policy_snapshots", "mcp_refs")
