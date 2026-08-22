"""创建 Phase 3 Review Workflow 数据契约

Revision ID: a8c3e5f7b9d1
Revises: d7f3a1c9e5b2
Create Date: 2026-08-22 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8c3e5f7b9d1"
down_revision: str | Sequence[str] | None = "d7f3a1c9e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Review、Step、Source、Dependency、Output、HITL 与 Artifact 表。"""
    op.create_table(
        "review_runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("research_question", sa.Text(), nullable=False),
        sa.Column("workflow_version", sa.String(length=50), nullable=False),
        sa.Column("model_profile_version", sa.String(length=50), nullable=False),
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("statistics_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_stage", sa.String(length=40), nullable=False),
        sa.Column("current_outline_output_id", sa.String(length=36), nullable=True),
        sa.Column("final_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("run_id"),
    )

    op.create_table(
        "run_steps",
        sa.Column("step_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("step_key", sa.String(length=50), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("input_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_run_steps_sequence_positive"),
        sa.CheckConstraint(
            "status IN ('pending','running','paused','succeeded','failed','cancelled')",
            name="ck_run_steps_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["review_runs.run_id"]),
        sa.PrimaryKeyConstraint("step_id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_steps_run_sequence"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_run_steps_run_idempotency"),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])

    op.create_table(
        "review_sources",
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("arxiv_id", sa.String(length=100), nullable=False),
        sa.Column("arxiv_version", sa.String(length=20), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("metadata_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=True),
        sa.Column("paper_version_id", sa.String(length=36), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rank >= 1", name="ck_review_sources_rank_positive"),
        sa.CheckConstraint(
            "status IN ('discovered','importing','ready','failed')",
            name="ck_review_sources_status",
        ),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.paper_id"]),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.version_id"]),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.run_id"]),
        sa.PrimaryKeyConstraint("source_id"),
        sa.UniqueConstraint(
            "review_run_id",
            "arxiv_id",
            "arxiv_version",
            name="uq_review_sources_run_arxiv_version",
        ),
        sa.UniqueConstraint("review_run_id", "rank", name="uq_review_sources_run_rank"),
    )
    op.create_index("ix_review_sources_review_run_id", "review_sources", ["review_run_id"])

    op.create_table(
        "run_dependencies",
        sa.Column("dependency_id", sa.String(length=36), nullable=False),
        sa.Column("parent_run_id", sa.String(length=36), nullable=False),
        sa.Column("dependency_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("target_run_id", sa.String(length=36), nullable=True),
        sa.Column("target_paper_version_id", sa.String(length=36), nullable=True),
        sa.Column("target_chunk_set_id", sa.String(length=36), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("satisfied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "dependency_type IN ('run','paper_version','chunk_set')",
            name="ck_run_dependencies_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','satisfied','failed')",
            name="ck_run_dependencies_status",
        ),
        sa.CheckConstraint(
            "(dependency_type = 'run' AND target_run_id IS NOT NULL "
            "AND target_paper_version_id IS NULL AND target_chunk_set_id IS NULL) OR "
            "(dependency_type = 'paper_version' AND target_run_id IS NULL "
            "AND target_paper_version_id IS NOT NULL AND target_chunk_set_id IS NULL) OR "
            "(dependency_type = 'chunk_set' AND target_run_id IS NULL "
            "AND target_paper_version_id IS NULL AND target_chunk_set_id IS NOT NULL)",
            name="ck_run_dependencies_exact_target",
        ),
        sa.ForeignKeyConstraint(["parent_run_id"], ["review_runs.run_id"]),
        sa.ForeignKeyConstraint(["target_chunk_set_id"], ["chunk_sets.chunk_set_id"]),
        sa.ForeignKeyConstraint(["target_paper_version_id"], ["paper_versions.version_id"]),
        sa.ForeignKeyConstraint(["target_run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("dependency_id"),
    )
    op.create_index("ix_run_dependencies_parent_run_id", "run_dependencies", ["parent_run_id"])
    op.create_index(
        "uq_run_dependencies_run_target_run",
        "run_dependencies",
        ["parent_run_id", "target_run_id"],
        unique=True,
        postgresql_where=sa.text("target_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_run_dependencies_run_target_version",
        "run_dependencies",
        ["parent_run_id", "target_paper_version_id"],
        unique=True,
        postgresql_where=sa.text("target_paper_version_id IS NOT NULL"),
    )
    op.create_index(
        "uq_run_dependencies_run_target_chunk_set",
        "run_dependencies",
        ["parent_run_id", "target_chunk_set_id"],
        unique=True,
        postgresql_where=sa.text("target_chunk_set_id IS NOT NULL"),
    )

    op.create_table(
        "review_outputs",
        sa.Column("output_id", sa.String(length=36), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("output_type", sa.String(length=30), nullable=False),
        sa.Column("output_key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_review_outputs_version_positive"),
        sa.CheckConstraint(
            "output_type IN ('search_strategy','evidence_matrix','outline','section',"
            "'consistency_report','final_review')",
            name="ck_review_outputs_type",
        ),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.run_id"]),
        sa.PrimaryKeyConstraint("output_id"),
        sa.UniqueConstraint(
            "review_run_id",
            "output_type",
            "output_key",
            "version",
            name="uq_review_outputs_run_type_key_version",
        ),
        sa.UniqueConstraint(
            "review_run_id", "idempotency_key", name="uq_review_outputs_run_idempotency"
        ),
    )
    op.create_index("ix_review_outputs_review_run_id", "review_outputs", ["review_run_id"])

    op.create_table(
        "human_input_requests",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("outline_output_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("allowed_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_input_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("request_version >= 1", name="ck_human_input_requests_version"),
        sa.CheckConstraint(
            "status IN ('open','resolved','cancelled')",
            name="ck_human_input_requests_status",
        ),
        sa.ForeignKeyConstraint(["outline_output_id"], ["review_outputs.output_id"]),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.run_id"]),
        sa.PrimaryKeyConstraint("request_id"),
        sa.UniqueConstraint(
            "request_id", "request_version", name="uq_human_input_requests_id_version"
        ),
        sa.UniqueConstraint(
            "review_run_id", "request_version", name="uq_human_input_requests_run_version"
        ),
    )
    op.create_index(
        "ix_human_input_requests_review_run_id",
        "human_input_requests",
        ["review_run_id"],
    )
    op.create_index(
        "uq_human_input_requests_one_open_per_run",
        "human_input_requests",
        ["review_run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "human_inputs",
        sa.Column("human_input_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("submitted_by", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('approve','edit','feedback')", name="ck_human_inputs_action"
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "request_version"],
            ["human_input_requests.request_id", "human_input_requests.request_version"],
            name="fk_human_inputs_request_version",
        ),
        sa.PrimaryKeyConstraint("human_input_id"),
        sa.UniqueConstraint("request_id", name="uq_human_inputs_request"),
        sa.UniqueConstraint(
            "submitted_by", "idempotency_key", name="uq_human_inputs_submitter_idempotency"
        ),
    )
    op.create_index("ix_human_inputs_submitted_by", "human_inputs", ["submitted_by"])

    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("review_run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", sa.String(length=30), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("source_output_id", sa.String(length=36), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_nonnegative"),
        sa.CheckConstraint(
            "artifact_type IN ('review_markdown','search_strategy','source_manifest',"
            "'evidence_matrix','bibliography','run_summary')",
            name="ck_artifacts_type",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"]),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.run_id"]),
        sa.ForeignKeyConstraint(["source_output_id"], ["review_outputs.output_id"]),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
        sa.UniqueConstraint(
            "review_run_id", "idempotency_key", name="uq_artifacts_run_idempotency"
        ),
    )
    op.create_index("ix_artifacts_content_hash", "artifacts", ["content_hash"])
    op.create_index("ix_artifacts_owner_id", "artifacts", ["owner_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_index("ix_artifacts_review_run_id", "artifacts", ["review_run_id"])

    # 三条循环引用在两端表建立后再添加。它们只保证目标存在；目标与当前
    # Review Run 的归属一致性由后续写入服务在短事务内校验。
    op.create_foreign_key(
        "fk_human_input_requests_resolved_input",
        "human_input_requests",
        "human_inputs",
        ["resolved_input_id"],
        ["human_input_id"],
    )
    op.create_foreign_key(
        "fk_review_runs_current_outline_output",
        "review_runs",
        "review_outputs",
        ["current_outline_output_id"],
        ["output_id"],
    )
    op.create_foreign_key(
        "fk_review_runs_final_artifact",
        "review_runs",
        "artifacts",
        ["final_artifact_id"],
        ["artifact_id"],
    )


def downgrade() -> None:
    """按引用逆序删除 Review Workflow 数据契约。"""
    op.drop_constraint("fk_review_runs_final_artifact", "review_runs", type_="foreignkey")
    op.drop_constraint("fk_review_runs_current_outline_output", "review_runs", type_="foreignkey")
    op.drop_constraint(
        "fk_human_input_requests_resolved_input",
        "human_input_requests",
        type_="foreignkey",
    )
    op.drop_table("artifacts")
    op.drop_table("human_inputs")
    op.drop_table("human_input_requests")
    op.drop_table("review_outputs")
    op.drop_table("run_dependencies")
    op.drop_table("review_sources")
    op.drop_table("run_steps")
    op.drop_table("review_runs")
