"""create projects table

修订 ID: a1b2c3d4
创建日期: 2026-08-19 07:40:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 projects 表。"""
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])


def downgrade() -> None:
    """删除 projects 表。"""
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")
