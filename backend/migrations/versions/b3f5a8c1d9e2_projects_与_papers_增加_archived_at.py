"""projects 与 papers 增加 archived_at 归档时间列

Revision ID: b3f5a8c1d9e2
Revises: c84f2d7a91e6
Create Date: 2026-08-20 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3f5a8c1d9e2"
down_revision: str | Sequence[str] | None = "c84f2d7a91e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 projects 和 papers 增加可空归档时间列，None 表示 active。"""
    op.add_column(
        "projects",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "papers",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """移除 projects 和 papers 的归档时间列。"""
    op.drop_column("papers", "archived_at")
    op.drop_column("projects", "archived_at")
