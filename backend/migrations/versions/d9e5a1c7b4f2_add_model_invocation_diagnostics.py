"""增加不含模型正文的调用诊断字段。

Revision ID: d9e5a1c7b4f2
Revises: c7d2f9a4e1b8
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e5a1c7b4f2"
down_revision: str | None = "c7d2f9a4e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FINISH_REASONS = (
    "stop",
    "length",
    "content_filter",
    "tool_calls",
    "function_call",
    "other",
)


def upgrade() -> None:
    op.add_column(
        "model_invocations",
        sa.Column("requested_max_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "model_invocations",
        sa.Column("finish_reason", sa.String(32), nullable=True),
    )
    op.add_column(
        "model_invocations",
        sa.Column("response_bytes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "model_invocations",
        sa.Column("response_sha256", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_model_invocation_requested_max_tokens",
        "model_invocations",
        "requested_max_tokens IS NULL OR requested_max_tokens > 0",
    )
    op.create_check_constraint(
        "ck_model_invocation_finish_reason",
        "model_invocations",
        f"finish_reason IS NULL OR finish_reason IN {_FINISH_REASONS}",
    )
    op.create_check_constraint(
        "ck_model_invocation_response_fingerprint",
        "model_invocations",
        "(response_bytes IS NULL AND response_sha256 IS NULL) OR "
        "(response_bytes >= 0 AND response_sha256 ~ '^[0-9a-f]{64}$')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_model_invocation_response_fingerprint",
        "model_invocations",
        type_="check",
    )
    op.drop_constraint(
        "ck_model_invocation_finish_reason",
        "model_invocations",
        type_="check",
    )
    op.drop_constraint(
        "ck_model_invocation_requested_max_tokens",
        "model_invocations",
        type_="check",
    )
    op.drop_column("model_invocations", "response_sha256")
    op.drop_column("model_invocations", "response_bytes")
    op.drop_column("model_invocations", "finish_reason")
    op.drop_column("model_invocations", "requested_max_tokens")
