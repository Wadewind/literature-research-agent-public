"""增加 review.v2 来源类型与来源筛选人工节点。

Revision ID: b2c4d6e8f1a3
Revises: a9e3d5f7b1c4
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c4d6e8f1a3"
down_revision: str | None = "a9e3d5f7b1c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展 ReviewSource，并让顺序人工输入支持来源筛选。"""
    op.add_column(
        "review_sources",
        sa.Column("source_kind", sa.String(length=20), nullable=False, server_default="arxiv"),
    )
    op.alter_column("review_sources", "arxiv_id", existing_type=sa.String(100), nullable=True)
    op.alter_column(
        "review_sources", "arxiv_version", existing_type=sa.String(20), nullable=True
    )
    op.drop_constraint("ck_review_sources_status", "review_sources", type_="check")
    op.create_check_constraint(
        "ck_review_sources_status",
        "review_sources",
        "status IN ('discovered','importing','ready','failed','rejected')",
    )
    op.create_check_constraint(
        "ck_review_sources_kind",
        "review_sources",
        "source_kind IN ('arxiv','project')",
    )
    op.create_check_constraint(
        "ck_review_sources_identity",
        "review_sources",
        "(source_kind = 'arxiv' AND arxiv_id IS NOT NULL AND arxiv_version IS NOT NULL) "
        "OR (source_kind = 'project' AND arxiv_id IS NULL AND arxiv_version IS NULL "
        "AND paper_id IS NOT NULL AND paper_version_id IS NOT NULL)",
    )
    op.alter_column("review_sources", "source_kind", server_default=None)

    op.add_column(
        "human_input_requests",
        sa.Column("request_kind", sa.String(length=30), nullable=False, server_default="outline"),
    )
    op.create_check_constraint(
        "ck_human_input_requests_kind",
        "human_input_requests",
        "request_kind IN ('outline','source_selection')",
    )
    op.alter_column("human_input_requests", "request_kind", server_default=None)
    op.drop_constraint("ck_human_inputs_action", "human_inputs", type_="check")
    op.create_check_constraint(
        "ck_human_inputs_action",
        "human_inputs",
        "action IN ('approve','edit','feedback','select_sources')",
    )


def downgrade() -> None:
    """恢复 review.v1 的 arXiv-only 来源与大纲人工输入约束。"""
    op.execute(
        "DELETE FROM human_inputs WHERE request_id IN "
        "(SELECT request_id FROM human_input_requests WHERE request_kind = 'source_selection')"
    )
    op.execute("DELETE FROM human_input_requests WHERE request_kind = 'source_selection'")
    op.drop_constraint("ck_human_inputs_action", "human_inputs", type_="check")
    op.create_check_constraint(
        "ck_human_inputs_action",
        "human_inputs",
        "action IN ('approve','edit','feedback')",
    )
    op.drop_constraint("ck_human_input_requests_kind", "human_input_requests", type_="check")
    op.drop_column("human_input_requests", "request_kind")

    op.drop_constraint("ck_review_sources_identity", "review_sources", type_="check")
    op.drop_constraint("ck_review_sources_kind", "review_sources", type_="check")
    op.drop_constraint("ck_review_sources_status", "review_sources", type_="check")
    op.execute("DELETE FROM review_sources WHERE source_kind = 'project' OR status = 'rejected'")
    op.create_check_constraint(
        "ck_review_sources_status",
        "review_sources",
        "status IN ('discovered','importing','ready','failed')",
    )
    op.alter_column(
        "review_sources", "arxiv_version", existing_type=sa.String(20), nullable=False
    )
    op.alter_column("review_sources", "arxiv_id", existing_type=sa.String(100), nullable=False)
    op.drop_column("review_sources", "source_kind")
