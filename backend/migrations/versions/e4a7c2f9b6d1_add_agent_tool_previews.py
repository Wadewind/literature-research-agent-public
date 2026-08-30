"""增加 Agent Tool 有界脱敏输入输出预览。

Revision ID: e4a7c2f9b6d1
Revises: d9e5a1c7b4f2
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a7c2f9b6d1"
down_revision: str | None = "d9e5a1c7b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_tool_calls", sa.Column("input_preview", sa.Text()))
    op.add_column(
        "agent_tool_calls",
        sa.Column(
            "input_preview_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("agent_tool_calls", sa.Column("output_preview", sa.Text()))
    op.add_column(
        "agent_tool_calls",
        sa.Column(
            "output_preview_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("agent_tool_calls", "input_preview_truncated", server_default=None)
    op.alter_column("agent_tool_calls", "output_preview_truncated", server_default=None)
    op.create_check_constraint(
        "ck_agent_tool_call_preview_flags",
        "agent_tool_calls",
        "(input_preview IS NOT NULL OR input_preview_truncated = false) AND "
        "(output_preview IS NOT NULL OR output_preview_truncated = false)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_tool_call_preview_flags",
        "agent_tool_calls",
        type_="check",
    )
    op.drop_column("agent_tool_calls", "output_preview_truncated")
    op.drop_column("agent_tool_calls", "output_preview")
    op.drop_column("agent_tool_calls", "input_preview_truncated")
    op.drop_column("agent_tool_calls", "input_preview")
