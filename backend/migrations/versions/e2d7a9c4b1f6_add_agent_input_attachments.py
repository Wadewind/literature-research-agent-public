"""增加 Agent 输入附件与消息引用。

Revision ID: e2d7a9c4b1f6
Revises: a4c9e2f7b1d5
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2d7a9c4b1f6"
down_revision: str | None = "a4c9e2f7b1d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_attachments",
        sa.Column("attachment_id", sa.String(36), primary_key=True),
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
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_agent_attachment_owner_idempotency"
        ),
        sa.CheckConstraint("version = 1", name="ck_agent_attachment_version"),
        sa.CheckConstraint(
            "size_bytes BETWEEN 0 AND 10485760", name="ck_agent_attachment_size"
        ),
        sa.CheckConstraint(
            "status IN ('available','deleted')", name="ck_agent_attachment_status"
        ),
        sa.CheckConstraint(
            "(status = 'available' AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND deleted_at IS NOT NULL)",
            name="ck_agent_attachment_state_fields",
        ),
    )
    op.create_index("ix_agent_attachments_owner_id", "agent_attachments", ["owner_id"])
    op.create_index(
        "ix_agent_attachments_project_id", "agent_attachments", ["project_id"]
    )
    op.create_index(
        "ix_agent_attachments_session_id", "agent_attachments", ["session_id"]
    )
    op.create_table(
        "agent_message_attachments",
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("agent_messages.message_id"),
            primary_key=True,
        ),
        sa.Column(
            "attachment_id",
            sa.String(36),
            sa.ForeignKey("agent_attachments.attachment_id"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "message_id", "ordinal", name="uq_agent_message_attachment_ordinal"
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 5", name="ck_agent_message_attachment_ordinal"
        ),
    )
    op.add_column(
        "agent_context_snapshots",
        sa.Column(
            "attachment_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("agent_context_snapshots", "attachment_refs", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_context_snapshots", "attachment_refs")
    op.drop_table("agent_message_attachments")
    op.drop_index("ix_agent_attachments_session_id", table_name="agent_attachments")
    op.drop_index("ix_agent_attachments_project_id", table_name="agent_attachments")
    op.drop_index("ix_agent_attachments_owner_id", table_name="agent_attachments")
    op.drop_table("agent_attachments")
