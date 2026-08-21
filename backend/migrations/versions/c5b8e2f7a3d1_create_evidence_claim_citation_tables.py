"""创建 evidence / claim_sets / claims / citations 四张表（切片 7）

- ``evidence``：一次 rag_answer Run 固化的可引用证据快照，denormalize
  paper/version/parse_revision/章节/页码/摘录；唯一约束
  ``(run_id, chunk_id)`` 兜底重复提交（一次 Run 中一个 Chunk 只固化
  一条 Evidence）；paper/version/parse_revision 为历史快照列，不建
  FK（历史 Evidence 不因后续移出、换版或归档而改变，ADR 0002）。
- ``claim_sets``：一次回答的结构化 Claim 集合，``run_id`` 唯一
  （一个 Run 只提交一个 ClaimSet）；Message 表切片 8 才建。
- ``claims``：段落级论述，唯一约束 ``(claim_set_id, sequence)``。
- ``citations``：Claim 与 Evidence 的关联，复合主键
  ``(claim_id, evidence_id)``，不存额外字段。

Revision ID: c5b8e2f7a3d1
Revises: f2a7b3c9d4e1
Create Date: 2026-08-21 06:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5b8e2f7a3d1"
down_revision: str | Sequence[str] | None = "f2a7b3c9d4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Evidence/ClaimSet/Claim/Citation 四张表。"""
    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("parse_revision_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("section_path", sa.String(length=255), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.chunk_id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("evidence_id"),
        sa.UniqueConstraint("run_id", "chunk_id", name="uq_evidence_run_chunk"),
    )
    op.create_index("ix_evidence_project_id", "evidence", ["project_id"])
    op.create_index("ix_evidence_run_id", "evidence", ["run_id"])

    op.create_table(
        "claim_sets",
        sa.Column("claim_set_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("answer_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("claim_set_id"),
        sa.UniqueConstraint("run_id", name="uq_claim_sets_run_id"),
    )

    op.create_table(
        "claims",
        sa.Column("claim_id", sa.String(length=36), nullable=False),
        sa.Column("claim_set_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["claim_set_id"], ["claim_sets.claim_set_id"]),
        sa.PrimaryKeyConstraint("claim_id"),
        sa.UniqueConstraint(
            "claim_set_id", "sequence", name="uq_claims_claim_set_sequence"
        ),
    )
    op.create_index("ix_claims_claim_set_id", "claims", ["claim_set_id"])

    op.create_table(
        "citations",
        sa.Column("claim_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.claim_id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.evidence_id"]),
        sa.PrimaryKeyConstraint("claim_id", "evidence_id"),
    )
    op.create_index("ix_citations_evidence_id", "citations", ["evidence_id"])


def downgrade() -> None:
    """按依赖逆序删除四张表。"""
    op.drop_table("citations")
    op.drop_table("claims")
    op.drop_table("claim_sets")
    op.drop_table("evidence")
