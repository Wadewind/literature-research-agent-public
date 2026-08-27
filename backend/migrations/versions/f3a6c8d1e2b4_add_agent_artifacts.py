"""增加 Agent Candidate 生命周期与正式 Artifact。

Revision ID: f3a6c8d1e2b4
Revises: b7d3e1f9a5c2
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a6c8d1e2b4"
down_revision: str | None = "b7d3e1f9a5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_candidate_size", "agent_artifact_candidates", type_="check")
    op.drop_constraint("ck_agent_candidate_status", "agent_artifact_candidates", type_="check")
    op.add_column("agent_artifact_candidates", sa.Column("tool_call_id", sa.String(255)))
    op.add_column("agent_artifact_candidates", sa.Column("storage_key", sa.String(500)))
    op.add_column("agent_artifact_candidates", sa.Column("sandbox_generation", sa.Integer()))
    op.add_column("agent_artifact_candidates", sa.Column("sandbox_fencing_token", sa.Integer()))
    op.add_column("agent_artifact_candidates", sa.Column("rejection_code", sa.String(100)))
    op.add_column(
        "agent_artifact_candidates",
        sa.Column("validated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "agent_artifact_candidates",
        sa.Column("committed_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_agent_candidate_size",
        "agent_artifact_candidates",
        "size_bytes BETWEEN 0 AND 10485760",
    )
    op.create_check_constraint(
        "ck_agent_candidate_status",
        "agent_artifact_candidates",
        "status IN ('staged','validated','committed','rejected')",
    )
    op.create_check_constraint(
        "ck_agent_candidate_state_fields",
        "agent_artifact_candidates",
        "(status = 'staged' AND tool_call_id IS NULL AND storage_key IS NULL "
        "AND sandbox_generation IS NULL AND sandbox_fencing_token IS NULL "
        "AND rejection_code IS NULL AND validated_at IS NULL AND committed_at IS NULL) "
        "OR (status = 'validated' AND tool_call_id IS NOT NULL "
        "AND storage_key IS NOT NULL AND sandbox_generation > 0 "
        "AND sandbox_fencing_token > 0 AND rejection_code IS NULL "
        "AND validated_at IS NOT NULL AND committed_at IS NULL) "
        "OR (status = 'committed' AND tool_call_id IS NOT NULL "
        "AND storage_key IS NOT NULL AND sandbox_generation > 0 "
        "AND sandbox_fencing_token > 0 AND rejection_code IS NULL "
        "AND validated_at IS NOT NULL AND committed_at IS NOT NULL) "
        "OR (status = 'rejected' AND tool_call_id IS NULL AND storage_key IS NULL "
        "AND sandbox_generation IS NULL AND sandbox_fencing_token IS NULL "
        "AND rejection_code IS NOT NULL AND validated_at IS NULL AND committed_at IS NULL)",
    )
    op.create_unique_constraint(
        "uq_agent_candidate_turn_tool_call",
        "agent_artifact_candidates",
        ["turn_run_id", "tool_call_id"],
    )
    op.create_table(
        "agent_artifacts",
        sa.Column("artifact_id", sa.String(36), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(255),
            sa.ForeignKey("agent_artifact_candidates.candidate_id"),
            unique=True,
            nullable=False,
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
            "turn_run_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_runs.turn_run_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(500), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes BETWEEN 0 AND 10485760", name="ck_agent_artifact_size"),
    )
    op.create_index("ix_agent_artifacts_owner_id", "agent_artifacts", ["owner_id"])
    op.create_index("ix_agent_artifacts_project_id", "agent_artifacts", ["project_id"])
    op.create_index("ix_agent_artifacts_session_id", "agent_artifacts", ["session_id"])
    op.create_index("ix_agent_artifacts_turn_run_id", "agent_artifacts", ["turn_run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_artifacts_turn_run_id", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_session_id", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_project_id", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_owner_id", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
    op.drop_constraint(
        "uq_agent_candidate_turn_tool_call",
        "agent_artifact_candidates",
        type_="unique",
    )
    op.drop_constraint(
        "ck_agent_candidate_state_fields",
        "agent_artifact_candidates",
        type_="check",
    )
    op.drop_constraint("ck_agent_candidate_status", "agent_artifact_candidates", type_="check")
    op.drop_constraint("ck_agent_candidate_size", "agent_artifact_candidates", type_="check")
    for name in (
        "committed_at",
        "validated_at",
        "rejection_code",
        "sandbox_fencing_token",
        "sandbox_generation",
        "storage_key",
        "tool_call_id",
    ):
        op.drop_column("agent_artifact_candidates", name)
    op.create_check_constraint(
        "ck_agent_candidate_size",
        "agent_artifact_candidates",
        "size_bytes BETWEEN 0 AND 1000000",
    )
    op.create_check_constraint(
        "ck_agent_candidate_status",
        "agent_artifact_candidates",
        "status = 'staged'",
    )
