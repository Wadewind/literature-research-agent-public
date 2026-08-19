"""document_parse_revisions 增加 degraded 与 warnings

Revision ID: 13497f1b8554
Revises: 8865966463a6
Create Date: 2026-08-20 01:14:31.750013

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '13497f1b8554'
down_revision: Union[str, Sequence[str], None] = '8865966463a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_foreign_key(
        "fk_document_elements_parent_element_id",
        "document_elements",
        "document_elements",
        ["parent_element_id"],
        ["element_id"],
        use_alter=True,
    )
    op.add_column(
        "document_parse_revisions",
        sa.Column("degraded", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "document_parse_revisions",
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("document_parse_revisions", "warnings")
    op.drop_column("document_parse_revisions", "degraded")
    op.drop_constraint(
        "fk_document_elements_parent_element_id",
        "document_elements",
        type_="foreignkey",
    )
