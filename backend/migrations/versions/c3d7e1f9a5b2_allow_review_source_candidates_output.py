"""允许持久化 review.v2 来源候选快照。

Revision ID: c3d7e1f9a5b2
Revises: b2c4d6e8f1a3
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d7e1f9a5b2"
down_revision: str | None = "b2c4d6e8f1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """把来源候选加入 ReviewOutput 的持久化类型约束。"""
    op.drop_constraint("ck_review_outputs_type", "review_outputs", type_="check")
    op.create_check_constraint(
        "ck_review_outputs_type",
        "review_outputs",
        "output_type IN ('search_strategy','evidence_matrix','outline','section',"
        "'consistency_report','final_review','source_candidates')",
    )


def downgrade() -> None:
    """删除 v2 来源筛选事实并恢复旧的 ReviewOutput 类型约束。"""
    op.execute(
        "UPDATE human_input_requests SET resolved_input_id = NULL "
        "WHERE request_kind = 'source_selection'"
    )
    op.execute(
        "DELETE FROM human_inputs WHERE request_id IN "
        "(SELECT request_id FROM human_input_requests WHERE request_kind = 'source_selection')"
    )
    op.execute("DELETE FROM human_input_requests WHERE request_kind = 'source_selection'")
    op.execute("DELETE FROM review_outputs WHERE output_type = 'source_candidates'")
    op.drop_constraint("ck_review_outputs_type", "review_outputs", type_="check")
    op.create_check_constraint(
        "ck_review_outputs_type",
        "review_outputs",
        "output_type IN ('search_strategy','evidence_matrix','outline','section',"
        "'consistency_report','final_review')",
    )
