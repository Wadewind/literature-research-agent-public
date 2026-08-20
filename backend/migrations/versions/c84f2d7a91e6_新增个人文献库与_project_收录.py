"""新增个人文献库与 Project 收录关系

Revision ID: c84f2d7a91e6
Revises: 7f3a9c2e1b54
Create Date: 2026-08-20 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c84f2d7a91e6"
down_revision: str | Sequence[str] | None = "7f3a9c2e1b54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """把 Project 直接归属迁移为 owner 文献库与显式收录关系。"""
    op.add_column(
        "paper_versions",
        sa.Column("owner_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "paper_versions",
        sa.Column("ingestion_run_id", sa.String(length=36), nullable=True),
    )
    op.execute(
        """
        UPDATE paper_versions AS version
        SET owner_id = paper.owner_id
        FROM papers AS paper
        WHERE paper.paper_id = version.paper_id
        """
    )
    op.execute(
        """
        UPDATE paper_versions AS version
        SET ingestion_run_id = source.run_id
        FROM (
            SELECT DISTINCT ON (input_payload ->> 'version_id')
                run_id,
                input_payload ->> 'version_id' AS version_id
            FROM runs
            WHERE run_type = 'ingestion'
              AND input_payload ? 'version_id'
            ORDER BY input_payload ->> 'version_id', created_at ASC
        ) AS source
        WHERE source.version_id = version.version_id
        """
    )
    op.alter_column("paper_versions", "owner_id", nullable=False)
    op.create_index("ix_paper_versions_owner_id", "paper_versions", ["owner_id"])
    op.create_foreign_key(
        "fk_paper_versions_ingestion_run_id_runs",
        "paper_versions",
        "runs",
        ["ingestion_run_id"],
        ["run_id"],
    )
    op.add_column(
        "papers",
        sa.Column("merged_into_paper_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_papers_merged_into_paper_id_papers",
        "papers",
        "papers",
        ["merged_into_paper_id"],
        ["paper_id"],
    )
    op.add_column(
        "paper_versions",
        sa.Column(
            "is_deduplication_canonical",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )

    op.create_table(
        "project_papers",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("selected_version_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.paper_id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"]),
        sa.ForeignKeyConstraint(["selected_version_id"], ["paper_versions.version_id"]),
        sa.PrimaryKeyConstraint("project_id", "paper_id"),
    )
    op.create_index(
        "ix_project_papers_selected_version_id",
        "project_papers",
        ["selected_version_id"],
    )

    # 旧版本允许同一 owner 在不同 Project 重复上传相同 PDF。
    # 先建立临时归并表，优先选择已有成功解析的最早 Version。
    op.execute(
        """
        CREATE TEMPORARY TABLE paper_version_merge ON COMMIT DROP AS
        WITH ranked AS (
            SELECT
                version_id,
                paper_id,
                owner_id,
                file_hash,
                FIRST_VALUE(version_id) OVER chosen AS canonical_version_id,
                FIRST_VALUE(paper_id) OVER chosen AS canonical_paper_id
            FROM paper_versions
            WINDOW chosen AS (
                PARTITION BY owner_id, file_hash
                ORDER BY (current_parse_revision_id IS NOT NULL) DESC, created_at ASC
            )
        )
        SELECT
            version_id AS duplicate_version_id,
            paper_id AS duplicate_paper_id,
            canonical_version_id,
            canonical_paper_id
        FROM ranked
        WHERE version_id <> canonical_version_id
        """
    )
    op.execute(
        """
        INSERT INTO project_papers (
            project_id, paper_id, selected_version_id, created_at
        )
        SELECT DISTINCT ON (
            paper.project_id,
            COALESCE(merge.canonical_paper_id, paper.paper_id)
        )
            paper.project_id,
            COALESCE(merge.canonical_paper_id, paper.paper_id),
            COALESCE(merge.canonical_version_id, version.version_id),
            paper.created_at
        FROM papers AS paper
        JOIN LATERAL (
            SELECT version_id
            FROM paper_versions
            WHERE paper_id = paper.paper_id
            ORDER BY created_at DESC
            LIMIT 1
        ) AS version ON TRUE
        LEFT JOIN paper_version_merge AS merge
          ON merge.duplicate_version_id = version.version_id
        ORDER BY
            paper.project_id,
            COALESCE(merge.canonical_paper_id, paper.paper_id),
            paper.created_at ASC
        """
    )

    # 无损保留历史 Paper、Version 和解析结果：只标记归并关系。
    op.execute(
        """
        UPDATE paper_versions AS version
        SET is_deduplication_canonical = FALSE
        FROM paper_version_merge AS merge
        WHERE version.version_id = merge.duplicate_version_id
        """
    )
    op.execute(
        """
        UPDATE papers AS paper
        SET merged_into_paper_id = merge.canonical_paper_id
        FROM paper_version_merge AS merge
        WHERE paper.paper_id = merge.duplicate_paper_id
        """
    )
    op.create_index(
        "uq_paper_versions_owner_file_hash_canonical",
        "paper_versions",
        ["owner_id", "file_hash"],
        unique=True,
        postgresql_where=sa.text("is_deduplication_canonical IS TRUE"),
    )

    op.add_column(
        "idempotency_keys",
        sa.Column("paper_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "idempotency_keys",
        sa.Column("version_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "idempotency_keys",
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
    )
    op.add_column(
        "idempotency_keys",
        sa.Column("reused", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "idempotency_keys",
        sa.Column("already_added", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        """
        UPDATE idempotency_keys AS key
        SET paper_id = COALESCE(
                merge.canonical_paper_id,
                run.input_payload ->> 'paper_id'
            ),
            version_id = COALESCE(
                merge.canonical_version_id,
                run.input_payload ->> 'version_id'
            ),
            status = run.status
        FROM runs AS run
        LEFT JOIN paper_version_merge AS merge
          ON run.input_payload ->> 'version_id' = merge.duplicate_version_id
        WHERE run.run_id = key.run_id
        """
    )
    op.alter_column("idempotency_keys", "run_id", nullable=True)
    op.create_foreign_key(
        "fk_idempotency_keys_paper_id_papers",
        "idempotency_keys",
        "papers",
        ["paper_id"],
        ["paper_id"],
    )
    op.create_foreign_key(
        "fk_idempotency_keys_version_id_paper_versions",
        "idempotency_keys",
        "paper_versions",
        ["version_id"],
        ["version_id"],
    )

    op.drop_index("ix_papers_project_id", table_name="papers")
    op.drop_constraint("papers_project_id_fkey", "papers", type_="foreignkey")
    op.drop_column("papers", "project_id")


def downgrade() -> None:
    """恢复旧的 Project 直接归属结构；无收录的 Paper 将保持 project_id 为空。"""
    op.add_column(
        "papers",
        sa.Column("project_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "papers_project_id_fkey",
        "papers",
        "projects",
        ["project_id"],
        ["project_id"],
    )
    op.execute(
        """
        UPDATE papers AS paper
        SET project_id = membership.project_id
        FROM (
            SELECT DISTINCT ON (paper_id) paper_id, project_id
            FROM project_papers
            ORDER BY paper_id, created_at ASC
        ) AS membership
        WHERE membership.paper_id = paper.paper_id
        """
    )
    op.create_index("ix_papers_project_id", "papers", ["project_id"])

    op.drop_constraint(
        "fk_idempotency_keys_version_id_paper_versions",
        "idempotency_keys",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_idempotency_keys_paper_id_papers",
        "idempotency_keys",
        type_="foreignkey",
    )
    for column in ("already_added", "reused", "status", "version_id", "paper_id"):
        op.drop_column("idempotency_keys", column)
    op.alter_column("idempotency_keys", "run_id", nullable=False)

    op.drop_index("ix_project_papers_selected_version_id", table_name="project_papers")
    op.drop_table("project_papers")
    op.drop_constraint(
        "fk_paper_versions_ingestion_run_id_runs",
        "paper_versions",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_paper_versions_owner_file_hash_canonical",
        table_name="paper_versions",
    )
    op.drop_column("paper_versions", "is_deduplication_canonical")
    op.drop_constraint(
        "fk_papers_merged_into_paper_id_papers",
        "papers",
        type_="foreignkey",
    )
    op.drop_column("papers", "merged_into_paper_id")
    op.drop_index("ix_paper_versions_owner_id", table_name="paper_versions")
    op.drop_column("paper_versions", "ingestion_run_id")
    op.drop_column("paper_versions", "owner_id")
