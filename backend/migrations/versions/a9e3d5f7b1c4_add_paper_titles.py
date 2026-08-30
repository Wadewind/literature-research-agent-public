"""为 Paper 增加带来源的论文标题并回填历史数据。

Revision ID: a9e3d5f7b1c4
Revises: e4a7c2f9b6d1
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9e3d5f7b1c4"
down_revision: str | None = "e4a7c2f9b6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加标题列，优先从 arXiv 快照、其次从当前解析标题回填。"""
    op.add_column("papers", sa.Column("title", sa.String(length=1000), nullable=True))
    op.add_column("papers", sa.Column("title_source", sa.String(length=32), nullable=True))

    op.execute(
        """
        WITH ranked_arxiv_titles AS (
            SELECT
                paper_id,
                LEFT(
                    regexp_replace(trim(metadata_snapshot ->> 'title'), '\\s+', ' ', 'g'),
                    1000
                ) AS title,
                row_number() OVER (
                    PARTITION BY paper_id
                    ORDER BY updated_at DESC, source_id
                ) AS position
            FROM review_sources
            WHERE paper_id IS NOT NULL
              AND nullif(trim(metadata_snapshot ->> 'title'), '') IS NOT NULL
        )
        UPDATE papers AS paper
        SET title = source.title,
            title_source = 'arxiv_metadata'
        FROM ranked_arxiv_titles AS source
        WHERE paper.paper_id = source.paper_id
          AND source.position = 1
        """
    )
    op.execute(
        """
        WITH ranked_parsed_titles AS (
            SELECT
                version.paper_id,
                LEFT(regexp_replace(trim(element.text), '\\s+', ' ', 'g'), 1000) AS title,
                row_number() OVER (
                    PARTITION BY version.paper_id
                    ORDER BY version.created_at DESC, element.sequence, element.element_id
                ) AS position
            FROM paper_versions AS version
            JOIN document_elements AS element
              ON element.revision_id = version.current_parse_revision_id
            WHERE element.element_type = 'title'
              AND nullif(trim(element.text), '') IS NOT NULL
        )
        UPDATE papers AS paper
        SET title = source.title,
            title_source = 'parsed_document'
        FROM ranked_parsed_titles AS source
        WHERE paper.paper_id = source.paper_id
          AND paper.title IS NULL
          AND source.position = 1
        """
    )

    op.create_check_constraint(
        "ck_papers_title_source_pair",
        "papers",
        "(title IS NULL AND title_source IS NULL) OR "
        "(title IS NOT NULL AND title_source IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_papers_title_source",
        "papers",
        "title_source IS NULL OR title_source IN ('arxiv_metadata','parsed_document')",
    )


def downgrade() -> None:
    """移除标题来源约束与字段。"""
    op.drop_constraint("ck_papers_title_source", "papers", type_="check")
    op.drop_constraint("ck_papers_title_source_pair", "papers", type_="check")
    op.drop_column("papers", "title_source")
    op.drop_column("papers", "title")
