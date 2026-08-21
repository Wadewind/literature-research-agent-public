"""chunks 增加 embedding 向量列与 search_vector 全文检索生成列

启用 pgvector 扩展，并为 chunks 表增加：
- ``embedding``：``vector(1024)`` 可空列。维度在迁移时固定为 1024
  （与 ``AGENT_EMBEDDING_DIMENSIONS`` 默认值一致），改维度需要新迁移，
  这是有意取舍；不建向量索引（首版精确检索，不建 HNSW）。
- ``search_vector``：``tsvector`` 生成列，
  ``GENERATED ALWAYS AS (to_tsvector('english', text)) STORED``，
  并建 GIN 索引（语料为英文学术论文，语言配置 english）。

Revision ID: f2a7b3c9d4e1
Revises: e9c4d2f8a1b7
Create Date: 2026-08-21 02:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "f2a7b3c9d4e1"
down_revision: str | Sequence[str] | None = "e9c4d2f8a1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """启用 vector 扩展并为 chunks 表增加检索列。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("chunks", sa.Column("embedding", Vector(1024), nullable=True))
    op.add_column(
        "chunks",
        sa.Column(
            "search_vector",
            TSVECTOR,
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_chunks_search_vector",
        "chunks",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """移除检索列；vector 扩展保留（可能被其他对象使用，不随回退删除）。"""
    op.drop_index("ix_chunks_search_vector", table_name="chunks")
    op.drop_column("chunks", "search_vector")
    op.drop_column("chunks", "embedding")
