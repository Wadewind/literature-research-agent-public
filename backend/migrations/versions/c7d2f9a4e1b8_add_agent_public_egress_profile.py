"""冻结 Agent public-egress profile 并绑定 Sandbox Lease。

Revision ID: c7d2f9a4e1b8
Revises: b6c1e4f8a2d7
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2f9a4e1b8"
down_revision: str | None = "b6c1e4f8a2d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_profile_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("network_profile_id", sa.String(100)))
    op.add_column(table_name, sa.Column("network_profile_version", sa.String(50)))
    op.add_column(table_name, sa.Column("network_profile_hash", sa.String(64)))


def upgrade() -> None:
    _add_profile_columns("agent_policy_snapshots")
    _add_profile_columns("agent_sandbox_leases")
    op.create_check_constraint(
        "ck_agent_policy_network_profile",
        "agent_policy_snapshots",
        "(network_profile_id IS NULL AND network_profile_version IS NULL "
        "AND network_profile_hash IS NULL AND network_enabled = false) OR "
        "(network_profile_id IS NOT NULL AND network_profile_version IS NOT NULL "
        "AND network_profile_hash IS NOT NULL AND network_enabled = true)",
    )
    op.create_check_constraint(
        "ck_agent_sandbox_lease_network_profile",
        "agent_sandbox_leases",
        "(network_profile_id IS NULL AND network_profile_version IS NULL "
        "AND network_profile_hash IS NULL) OR "
        "(network_profile_id IS NOT NULL AND network_profile_version IS NOT NULL "
        "AND network_profile_hash IS NOT NULL)",
    )
    for table_name in ("agent_artifact_candidates", "agent_artifacts"):
        op.add_column(table_name, sa.Column("source_url", sa.String(2048)))
        op.add_column(table_name, sa.Column("source_url_hash", sa.String(64)))
    op.create_check_constraint(
        "ck_agent_candidate_source",
        "agent_artifact_candidates",
        "(source_url IS NULL AND source_url_hash IS NULL) OR "
        "(source_url IS NOT NULL AND source_url_hash IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_agent_artifact_source",
        "agent_artifacts",
        "(source_url IS NULL AND source_url_hash IS NULL) OR "
        "(source_url IS NOT NULL AND source_url_hash IS NOT NULL)",
    )


def _drop_profile_columns(table_name: str) -> None:
    op.drop_column(table_name, "network_profile_hash")
    op.drop_column(table_name, "network_profile_version")
    op.drop_column(table_name, "network_profile_id")


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_artifact_source", "agent_artifacts", type_="check"
    )
    op.drop_constraint(
        "ck_agent_candidate_source", "agent_artifact_candidates", type_="check"
    )
    for table_name in ("agent_artifacts", "agent_artifact_candidates"):
        op.drop_column(table_name, "source_url_hash")
        op.drop_column(table_name, "source_url")
    op.drop_constraint(
        "ck_agent_sandbox_lease_network_profile",
        "agent_sandbox_leases",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_policy_network_profile",
        "agent_policy_snapshots",
        type_="check",
    )
    _drop_profile_columns("agent_sandbox_leases")
    _drop_profile_columns("agent_policy_snapshots")
