"""新建 chunk_sets / chunks / chunk_element_links 切分结果表

ChunkSet 是某个 Parse Revision 在特定 Chunk Profile 下的版本化切分
结果（唯一约束 parse_revision_id + profile_hash 保证 Effectively
Once）；Chunk 是面向检索的有序文本块；chunk_element_links 把 Chunk
回溯到来源 Element。本迁移不建 search_vector/embedding 列（切片 5
再加，避免二次迁移 chunk 表）。

Revision ID: e9c4d2f8a1b7
Revises: d6e1f7a3b9c2
Create Date: 2026-08-20 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e9c4d2f8a1b7"
down_revision: str | Sequence[str] | None = "d6e1f7a3b9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 chunk_sets、chunks 与 chunk_element_links 表。"""
    op.create_table(
        "chunk_sets",
        sa.Column("chunk_set_id", sa.String(length=36), nullable=False),
        sa.Column("parse_revision_id", sa.String(length=36), nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parse_revision_id"], ["document_parse_revisions.revision_id"]
        ),
        sa.PrimaryKeyConstraint("chunk_set_id"),
        sa.UniqueConstraint(
            "parse_revision_id", "profile_hash", name="uq_chunk_sets_revision_profile"
        ),
    )
    op.create_index(
        op.f("ix_chunk_sets_parse_revision_id"),
        "chunk_sets",
        ["parse_revision_id"],
        unique=False,
    )
    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_set_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("section_path", sa.String(length=255), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["chunk_set_id"], ["chunk_sets.chunk_set_id"]),
        sa.PrimaryKeyConstraint("chunk_id"),
        sa.UniqueConstraint(
            "chunk_set_id", "sequence", name="uq_chunks_chunk_set_sequence"
        ),
    )
    op.create_index(
        op.f("ix_chunks_chunk_set_id"), "chunks", ["chunk_set_id"], unique=False
    )
    op.create_table(
        "chunk_element_links",
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("element_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.chunk_id"]),
        sa.ForeignKeyConstraint(["element_id"], ["document_elements.element_id"]),
        sa.PrimaryKeyConstraint("chunk_id", "element_id"),
    )
    op.create_index(
        op.f("ix_chunk_element_links_element_id"),
        "chunk_element_links",
        ["element_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除切分结果表。"""
    op.drop_index(
        op.f("ix_chunk_element_links_element_id"), table_name="chunk_element_links"
    )
    op.drop_table("chunk_element_links")
    op.drop_index(op.f("ix_chunks_chunk_set_id"), table_name="chunks")
    op.drop_table("chunks")
    op.drop_index(op.f("ix_chunk_sets_parse_revision_id"), table_name="chunk_sets")
    op.drop_table("chunk_sets")
