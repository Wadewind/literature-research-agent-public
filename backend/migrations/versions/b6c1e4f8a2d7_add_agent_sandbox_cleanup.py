"""增加 Sandbox 退役状态与幂等清理补偿事实。

Revision ID: b6c1e4f8a2d7
Revises: a8d2f6c4e1b9
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6c1e4f8a2d7"
down_revision: str | None = "a8d2f6c4e1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_agent_sandbox_lease_status",
        "agent_sandbox_leases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_sandbox_lease_status",
        "agent_sandbox_leases",
        "status IN ('active','dirty','retired')",
    )
    op.create_table(
        "agent_sandbox_cleanups",
        sa.Column("cleanup_id", sa.String(64), primary_key=True),
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
        sa.Column("sandbox_id", sa.String(255), nullable=False, unique=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner_id", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_summary", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("generation >= 1", name="ck_sandbox_cleanup_generation"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_sandbox_cleanup_fence"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_sandbox_cleanup_attempts"),
        sa.CheckConstraint(
            "reason IN ('rotation','candidate_rejected','dirty','expired','session_closed')",
            name="ck_sandbox_cleanup_reason",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded')",
            name="ck_sandbox_cleanup_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND lease_owner_id IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND lease_owner_id IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'succeeded' AND lease_owner_id IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NOT NULL)",
            name="ck_sandbox_cleanup_state_fields",
        ),
    )
    op.create_index(
        "ix_agent_sandbox_cleanups_project_id",
        "agent_sandbox_cleanups",
        ["project_id"],
    )
    op.create_index(
        "ix_agent_sandbox_cleanups_session_id",
        "agent_sandbox_cleanups",
        ["session_id"],
    )
    op.create_index(
        "ix_agent_sandbox_cleanups_status",
        "agent_sandbox_cleanups",
        ["status"],
    )
    op.create_index(
        "ix_agent_sandbox_cleanups_next_attempt_at",
        "agent_sandbox_cleanups",
        ["next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_sandbox_cleanups_next_attempt_at",
        table_name="agent_sandbox_cleanups",
    )
    op.drop_index(
        "ix_agent_sandbox_cleanups_status",
        table_name="agent_sandbox_cleanups",
    )
    op.drop_index(
        "ix_agent_sandbox_cleanups_session_id",
        table_name="agent_sandbox_cleanups",
    )
    op.drop_index(
        "ix_agent_sandbox_cleanups_project_id",
        table_name="agent_sandbox_cleanups",
    )
    op.drop_table("agent_sandbox_cleanups")
    # 旧版本不认识 retired；先保守降级为 dirty，避免有真实租约时 CHECK 重建失败。
    op.execute(
        sa.text(
            "UPDATE agent_sandbox_leases SET status = 'dirty' "
            "WHERE status = 'retired'"
        )
    )
    op.drop_constraint(
        "ck_agent_sandbox_lease_status",
        "agent_sandbox_leases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_sandbox_lease_status",
        "agent_sandbox_leases",
        "status IN ('active','dirty')",
    )
