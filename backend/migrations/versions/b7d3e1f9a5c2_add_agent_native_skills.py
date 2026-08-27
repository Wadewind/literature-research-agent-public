"""增加 Agent Native Skills 业务事实。

Revision ID: b7d3e1f9a5c2
Revises: f6a2c9d4e7b1
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d3e1f9a5c2"
down_revision: str | None = "f6a2c9d4e7b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_policy_snapshots",
        sa.Column(
            "skill_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("agent_policy_snapshots", "skill_refs", server_default=None)
    op.create_table(
        "agent_owner_skills",
        sa.Column("skill_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_agent_owner_skills_owner_name"),
        sa.UniqueConstraint(
            "skill_id", "owner_id", name="uq_agent_owner_skills_identity_owner"
        ),
    )
    op.create_index("ix_agent_owner_skills_owner_id", "agent_owner_skills", ["owner_id"])
    op.create_table(
        "agent_owner_skill_versions",
        sa.Column("skill_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("required_tool_names", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id", "owner_id"],
            ["agent_owner_skills.skill_id", "agent_owner_skills.owner_id"],
            name="fk_agent_owner_skill_versions_identity_owner",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_owner_skill_versions_version"),
    )
    op.create_index(
        "ix_agent_owner_skill_versions_owner_id",
        "agent_owner_skill_versions",
        ["owner_id"],
    )
    op.create_table(
        "agent_skill_profiles",
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
        sa.CheckConstraint("revision >= 1", name="ck_agent_skill_profiles_revision"),
        sa.UniqueConstraint(
            "session_id", "revision", name="uq_agent_skill_profiles_session_revision"
        ),
    )
    op.create_index("ix_agent_skill_profiles_owner_id", "agent_skill_profiles", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_skill_profiles_owner_id", table_name="agent_skill_profiles")
    op.drop_table("agent_skill_profiles")
    op.drop_index(
        "ix_agent_owner_skill_versions_owner_id",
        table_name="agent_owner_skill_versions",
    )
    op.drop_table("agent_owner_skill_versions")
    op.drop_index("ix_agent_owner_skills_owner_id", table_name="agent_owner_skills")
    op.drop_table("agent_owner_skills")
    op.drop_column("agent_policy_snapshots", "skill_refs")
