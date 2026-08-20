"""新建 model_invocations 模型调用记录表

记录每次模型调用的能力、Provider、模型、状态、token 用量、延迟与错误
分类，不保存 Prompt 或响应内容。run_id 可空，切片 5/8 执行器接线时填充。

Revision ID: d6e1f7a3b9c2
Revises: b3f5a8c1d9e2
Create Date: 2026-08-20 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e1f7a3b9c2"
down_revision: str | Sequence[str] | None = "b3f5a8c1d9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 model_invocations 表及 run_id 索引。"""
    op.create_table(
        "model_invocations",
        sa.Column("invocation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("capability", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("invocation_id"),
    )
    op.create_index(
        op.f("ix_model_invocations_run_id"),
        "model_invocations",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除 model_invocations 表。"""
    op.drop_index(op.f("ix_model_invocations_run_id"), table_name="model_invocations")
    op.drop_table("model_invocations")
