"""创建 conversations / conversation_scope_papers / messages 三张表（切片 8）

- ``conversations``：Project 内的问答会话，scope 只有
  ``project`` / ``selected_papers`` 两值且创建后不可修改；
  ``active_run_id`` 是会话级单活跃 Run 的认领字段（条件更新认领，
  终态清理），可空。
- ``conversation_scope_papers``：``selected_papers`` 模式创建时解析
  固化的默认范围 ``{paper_id, version_id}``，复合主键
  ``(conversation_id, paper_id)``；paper/version 为固化快照列，不建
  FK（历史范围不因后续移出或换版而改变，与 evidence 快照列同理）。
- ``messages``：会话内消息，``sequence`` 严格递增，唯一约束
  ``(conversation_id, sequence)``；``run_id`` 关联 rag_answer Run
  （user 消息关联其触发的 Run，assistant 消息关联产生它的 Run），
  ``claim_set_id`` 仅 assistant 消息。

Revision ID: d7f3a1c9e5b2
Revises: c5b8e2f7a3d1
Create Date: 2026-08-21 08:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f3a1c9e5b2"
down_revision: str | Sequence[str] | None = "c5b8e2f7a3d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Conversation 相关三张表。"""
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("scope_mode", sa.String(length=20), nullable=False),
        sa.Column("active_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["active_run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"]),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])

    op.create_table(
        "conversation_scope_papers",
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"]
        ),
        sa.PrimaryKeyConstraint(
            "conversation_id", "paper_id", name="pk_conversation_scope_papers"
        ),
    )

    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("claim_set_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["claim_set_id"], ["claim_sets.claim_set_id"]),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"]
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("message_id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_messages_conversation_sequence"
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_run_id", "messages", ["run_id"])


def downgrade() -> None:
    """按依赖逆序删除三张表。"""
    op.drop_table("messages")
    op.drop_table("conversation_scope_papers")
    op.drop_table("conversations")
