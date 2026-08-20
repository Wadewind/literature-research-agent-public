"""paper_versions 增加 display_filename

Revision ID: 7f3a9c2e1b54
Revises: 28a3aeb62280
Create Date: 2026-08-20 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7f3a9c2e1b54'
down_revision: Union[str, Sequence[str], None] = '28a3aeb62280'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "paper_versions",
        sa.Column(
            "display_filename",
            sa.String(length=255),
            server_default="paper.pdf",
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("paper_versions", "display_filename")
