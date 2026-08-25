"""SQLAlchemy ORM 模型。"""

from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import Computed


class Base(DeclarativeBase):
    """ORM 基类。"""


class AgentSessionORM(Base):
    """绑定 owner/Project 的持续 Agent 会话。"""

    __tablename__ = "agent_sessions"
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"), index=True, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    active_turn_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "agent_turn_runs.turn_run_id",
            use_alter=True,
            name="fk_agent_sessions_active_turn",
        ),
        nullable=True,
    )
    next_message_sequence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('active','closed')", name="ck_agent_sessions_status"),
    )


class AgentMessageORM(Base):
    """Session 内有序的用户/助手业务消息。"""

    __tablename__ = "agent_messages"
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.session_id"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    turn_run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_agent_messages_sequence"),
        CheckConstraint("role IN ('user','assistant')", name="ck_agent_messages_role"),
        UniqueConstraint("session_id", "sequence", name="uq_agent_messages_session_sequence"),
        UniqueConstraint(
            "session_id", "idempotency_key", name="uq_agent_messages_session_idempotency"
        ),
    )


class AgentTurnRunORM(Base):
    """通用 Run 的 Agent Turn 一对一扩展。"""

    __tablename__ = "agent_turn_runs"
    turn_run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.session_id"), index=True, nullable=False
    )
    user_message_id: Mapped[str] = mapped_column(
        ForeignKey(
            "agent_messages.message_id",
            use_alter=True,
            name="fk_agent_turn_runs_user_message",
        ),
        unique=True,
        nullable=False,
    )
    context_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey(
            "agent_context_snapshots.snapshot_id",
            use_alter=True,
            name="fk_agent_turn_runs_context_snapshot",
        ),
        unique=True,
        nullable=False,
    )
    policy_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey(
            "agent_policy_snapshots.snapshot_id",
            use_alter=True,
            name="fk_agent_turn_runs_policy_snapshot",
        ),
        unique=True,
        nullable=False,
    )


class AgentContextSnapshotORM(Base):
    """不可变的 Turn 授权上下文引用。"""

    __tablename__ = "agent_context_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.session_id"), index=True, nullable=False
    )
    turn_run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), unique=True, nullable=False)
    user_message_id: Mapped[str] = mapped_column(
        ForeignKey(
            "agent_messages.message_id",
            use_alter=True,
            name="fk_agent_context_snapshots_user_message",
        ),
        nullable=False,
    )
    history_through_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    project_index_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    review_output_id: Mapped[str] = mapped_column(
        ForeignKey("review_outputs.output_id"), nullable=False
    )
    artifact_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentPolicySnapshotORM(Base):
    """不可变的 Turn 能力策略。"""

    __tablename__ = "agent_policy_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.session_id"), index=True, nullable=False
    )
    turn_run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), unique=True, nullable=False)
    allowed_tool_names: Mapped[list] = mapped_column(JSONB, nullable=False)
    allowed_skill_names: Mapped[list] = mapped_column(JSONB, nullable=False)
    network_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sandbox_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_model_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRuntimeSessionBindingORM(Base):
    """业务 Session 到 opaque Runtime Thread/Workspace 的映射。"""

    __tablename__ = "agent_runtime_session_bindings"
    binding_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.session_id"), index=True, nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_thread_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    runtime_workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_agent_runtime_session_generation"),
        UniqueConstraint("session_id", "generation", name="uq_agent_runtime_session_generation"),
    )


class AgentRuntimeTurnBindingORM(Base):
    """业务 Turn 到 opaque Runtime Execution/Checkpoint 的映射。"""

    __tablename__ = "agent_runtime_turn_bindings"
    turn_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turn_runs.turn_run_id"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.session_id"), index=True, nullable=False
    )
    session_binding_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runtime_session_bindings.binding_id"), nullable=False
    )
    runtime_execution_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    runtime_checkpoint_id: Mapped[str] = mapped_column(String(255), nullable=False)


class AgentArtifactCandidateORM(Base):
    """Runtime 候选产物；不是正式 Artifact。"""

    __tablename__ = "agent_artifact_candidates"
    candidate_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"), index=True, nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.session_id"), index=True, nullable=False
    )
    turn_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turn_runs.turn_run_id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    content_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("size_bytes BETWEEN 0 AND 1000000", name="ck_agent_candidate_size"),
        CheckConstraint("status = 'staged'", name="ck_agent_candidate_status"),
        UniqueConstraint("turn_run_id", "content_hash", name="uq_agent_candidate_turn_hash"),
    )


class ProjectORM(Base):
    """Project 的持久化映射。"""

    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RunORM(Base):
    """Run 的持久化映射。"""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"),
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    result_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    event_sequence: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class EventORM(Base):
    """Run Event 的持久化映射。"""

    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"),
        index=True,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    event_version: Mapped[str] = mapped_column(String(10), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_events_run_id_sequence"),)


class PaperORM(Base):
    """Paper 的持久化映射。"""

    __tablename__ = "papers"

    paper_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    merged_into_paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.paper_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class PaperVersionORM(Base):
    """PaperVersion 的持久化映射。"""

    __tablename__ = "paper_versions"

    version_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.paper_id"),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    display_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="paper.pdf",
    )
    current_parse_revision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("document_parse_revisions.revision_id", use_alter=True),
        nullable=True,
    )
    ingestion_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.run_id"),
        nullable=True,
    )
    is_deduplication_canonical: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "uq_paper_versions_owner_file_hash_canonical",
            "owner_id",
            "file_hash",
            unique=True,
            postgresql_where=is_deduplication_canonical.is_(True),
        ),
    )


class ProjectPaperORM(Base):
    """Project 对个人文献库 Paper 的收录关系。"""

    __tablename__ = "project_papers"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"),
        primary_key=True,
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.paper_id"),
        primary_key=True,
    )
    selected_version_id: Mapped[str] = mapped_column(
        ForeignKey("paper_versions.version_id"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class DocumentParseRevisionORM(Base):
    """Document Parse Revision 的持久化映射。"""

    __tablename__ = "document_parse_revisions"

    revision_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("paper_versions.version_id"),
        index=True,
        nullable=False,
    )
    parser_name: Mapped[str] = mapped_column(String(50), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    parser_profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    degraded: Mapped[bool] = mapped_column(default=False, nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "parser_profile_hash",
            name="uq_parse_revisions_version_profile",
        ),
    )


class DocumentElementORM(Base):
    """Document Element 的持久化映射。"""

    __tablename__ = "document_elements"

    element_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("document_parse_revisions.revision_id"),
        index=True,
        nullable=False,
    )
    element_type: Mapped[str] = mapped_column(String(30), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    parent_element_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("document_elements.element_id", use_alter=True),
        nullable=True,
    )
    section_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    __table_args__ = (
        UniqueConstraint("revision_id", "sequence", name="uq_elements_revision_sequence"),
    )


class ElementSourceLocationORM(Base):
    """Element 来源定位的持久化映射。"""

    __tablename__ = "element_source_locations"

    location_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    element_id: Mapped[str] = mapped_column(
        ForeignKey("document_elements.element_id"),
        index=True,
        nullable=False,
    )
    page: Mapped[int] = mapped_column(nullable=False)
    bbox: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    parser_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    char_range: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class QueueOutboxORM(Base):
    """Queue Outbox 的持久化映射。"""

    __tablename__ = "queue_outbox"

    outbox_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class RunAttemptORM(Base):
    """Run Attempt 的持久化映射。"""

    __tablename__ = "run_attempts"

    attempt_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"),
        index=True,
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "attempt_number", name="uq_run_attempts_run_attempt"),
    )


class IdempotencyKeyORM(Base):
    """API 幂等键的持久化映射。"""

    __tablename__ = "idempotency_keys"

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.run_id"),
        nullable=True,
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.paper_id"),
        nullable=True,
    )
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_versions.version_id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    reused: Mapped[bool] = mapped_column(default=False, nullable=False)
    already_added: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class ChunkSetORM(Base):
    """ChunkSet 的持久化映射。"""

    __tablename__ = "chunk_sets"

    chunk_set_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    parse_revision_id: Mapped[str] = mapped_column(
        ForeignKey("document_parse_revisions.revision_id"),
        index=True,
        nullable=False,
    )
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "parse_revision_id",
            "profile_hash",
            name="uq_chunk_sets_revision_profile",
        ),
    )


class ChunkORM(Base):
    """Chunk 的持久化映射（含切片 5 的 embedding/search_vector 检索列）。"""

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    chunk_set_id: Mapped[str] = mapped_column(
        ForeignKey("chunk_sets.chunk_set_id"),
        index=True,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_start: Mapped[int | None] = mapped_column(nullable=True)
    page_end: Mapped[int | None] = mapped_column(nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 维度在迁移中固定为 1024；改维度需要新迁移（有意取舍，见阶段 Spec）
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    # 全文检索生成列：由数据库从 text 派生，应用层只读
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("chunk_set_id", "sequence", name="uq_chunks_chunk_set_sequence"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )


class ChunkElementLinkORM(Base):
    """Chunk 到 Element 有序映射的持久化映射。"""

    __tablename__ = "chunk_element_links"

    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.chunk_id"),
        primary_key=True,
    )
    element_id: Mapped[str] = mapped_column(
        ForeignKey("document_elements.element_id"),
        index=True,
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)


class ModelInvocationORM(Base):
    """模型调用记录的持久化映射（不含 Prompt/响应内容）。"""

    __tablename__ = "model_invocations"

    invocation_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.run_id"),
        index=True,
        nullable=True,
    )
    capability: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class EvidenceORM(Base):
    """Evidence 的持久化映射（一次 Run 固化的可引用证据快照）。

    paper/version/parse_revision 为 denormalize 的历史快照列，不建 FK：
    历史 Evidence 不因后续移出、换版或归档而改变（ADR 0002）。
    """

    __tablename__ = "evidence"

    evidence_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"),
        index=True,
        nullable=False,
    )
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    paper_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parse_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.chunk_id"),
        nullable=False,
    )
    section_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_start: Mapped[int | None] = mapped_column(nullable=True)
    page_end: Mapped[int | None] = mapped_column(nullable=True)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        # 一次 Run 中一个 Chunk 只固化一条 Evidence（Effectively Once 兜底）
        UniqueConstraint("run_id", "chunk_id", name="uq_evidence_run_chunk"),
    )


class ClaimSetORM(Base):
    """ClaimSet 的持久化映射（一个生成结果 Run 至多一个）。"""

    __tablename__ = "claim_sets"

    claim_set_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"),
        nullable=False,
    )
    answer_status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_claim_sets_run_id"),
    )


class ClaimORM(Base):
    """Claim 的持久化映射（回答中的段落级论述）。"""

    __tablename__ = "claims"

    claim_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    claim_set_id: Mapped[str] = mapped_column(
        ForeignKey("claim_sets.claim_set_id"),
        index=True,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("claim_set_id", "sequence", name="uq_claims_claim_set_sequence"),
    )


class CitationORM(Base):
    """Citation 的持久化映射（Claim 与 Evidence 的关联，无额外字段）。"""

    __tablename__ = "citations"

    claim_id: Mapped[str] = mapped_column(
        ForeignKey("claims.claim_id"),
        primary_key=True,
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence.evidence_id"),
        index=True,
        primary_key=True,
    )


class ConversationORM(Base):
    """Conversation 的持久化映射（切片 8）。

    ``active_run_id`` 是会话级单活跃 Run 的认领字段：提交问题时条件
    更新认领（``WHERE active_run_id IS NULL``），Run 终态时清理。
    """

    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scope_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    active_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.run_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class ConversationScopePaperORM(Base):
    """Conversation 固化默认范围的持久化映射。

    paper/version 为创建时解析固化的快照列，不建 FK（历史范围不因
    后续移出或换版而改变）。
    """

    __tablename__ = "conversation_scope_papers"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id"),
        primary_key=True,
    )
    paper_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)


class MessageORM(Base):
    """Message 的持久化映射（会话内 sequence 严格递增）。"""

    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id"),
        index=True,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.run_id"),
        index=True,
        nullable=True,
    )
    claim_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("claim_sets.claim_set_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_messages_conversation_sequence"
        ),
    )


class ReviewRunORM(Base):
    """通用 Run 的固定综述 Workflow 扩展记录。"""

    __tablename__ = "review_runs"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"),
        primary_key=True,
    )
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_profile_version: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_versions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    statistics_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    current_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    current_outline_output_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "review_outputs.output_id",
            use_alter=True,
            name="fk_review_runs_current_outline_output",
        ),
        nullable=True,
    )
    final_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "artifacts.artifact_id",
            use_alter=True,
            name="fk_review_runs_final_artifact",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunStepORM(Base):
    """Review Run 内的一次可观察 Step 执行。"""

    __tablename__ = "run_steps"

    step_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.run_id"), index=True, nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(50), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    input_refs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_refs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_run_steps_sequence_positive"),
        CheckConstraint(
            "status IN ('pending','running','paused','succeeded','failed','cancelled')",
            name="ck_run_steps_status",
        ),
        UniqueConstraint("run_id", "sequence", name="uq_run_steps_run_sequence"),
        UniqueConstraint("run_id", "idempotency_key", name="uq_run_steps_run_idempotency"),
    )


class ReviewSourceORM(Base):
    """Review Run 自动纳入的 arXiv 来源。"""

    __tablename__ = "review_sources"

    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.run_id"), index=True, nullable=False
    )
    arxiv_id: Mapped[str] = mapped_column(String(100), nullable=False)
    arxiv_version: Mapped[str] = mapped_column(String(20), nullable=False)
    rank: Mapped[int] = mapped_column(nullable=False)
    metadata_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.paper_id"), nullable=True)
    paper_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_versions.version_id"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("rank >= 1", name="ck_review_sources_rank_positive"),
        CheckConstraint(
            "status IN ('discovered','importing','ready','failed')",
            name="ck_review_sources_status",
        ),
        UniqueConstraint(
            "review_run_id",
            "arxiv_id",
            "arxiv_version",
            name="uq_review_sources_run_arxiv_version",
        ),
        UniqueConstraint("review_run_id", "rank", name="uq_review_sources_run_rank"),
    )


class RunDependencyORM(Base):
    """Review Run 对 Run/PaperVersion/ChunkSet 的受限依赖。"""

    __tablename__ = "run_dependencies"

    dependency_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.run_id"), index=True, nullable=False
    )
    dependency_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    target_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id"), nullable=True)
    target_paper_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_versions.version_id"), nullable=True
    )
    target_chunk_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("chunk_sets.chunk_set_id"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "dependency_type IN ('run','paper_version','chunk_set')",
            name="ck_run_dependencies_type",
        ),
        CheckConstraint(
            "status IN ('pending','satisfied','failed')",
            name="ck_run_dependencies_status",
        ),
        CheckConstraint(
            "(dependency_type = 'run' AND target_run_id IS NOT NULL "
            "AND target_paper_version_id IS NULL AND target_chunk_set_id IS NULL) OR "
            "(dependency_type = 'paper_version' AND target_run_id IS NULL "
            "AND target_paper_version_id IS NOT NULL AND target_chunk_set_id IS NULL) OR "
            "(dependency_type = 'chunk_set' AND target_run_id IS NULL "
            "AND target_paper_version_id IS NULL AND target_chunk_set_id IS NOT NULL)",
            name="ck_run_dependencies_exact_target",
        ),
        Index(
            "uq_run_dependencies_run_target_run",
            "parent_run_id",
            "target_run_id",
            unique=True,
            postgresql_where=target_run_id.is_not(None),
        ),
        Index(
            "uq_run_dependencies_run_target_version",
            "parent_run_id",
            "target_paper_version_id",
            unique=True,
            postgresql_where=target_paper_version_id.is_not(None),
        ),
        Index(
            "uq_run_dependencies_run_target_chunk_set",
            "parent_run_id",
            "target_chunk_set_id",
            unique=True,
            postgresql_where=target_chunk_set_id.is_not(None),
        ),
    )


class ReviewOutputORM(Base):
    """追加写入的版本化结构化 Review 产物。"""

    __tablename__ = "review_outputs"

    output_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.run_id"), index=True, nullable=False
    )
    output_type: Mapped[str] = mapped_column(String(30), nullable=False)
    output_key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_review_outputs_version_positive"),
        CheckConstraint(
            "output_type IN ('search_strategy','evidence_matrix','outline','section',"
            "'consistency_report','final_review')",
            name="ck_review_outputs_type",
        ),
        UniqueConstraint(
            "review_run_id",
            "output_type",
            "output_key",
            "version",
            name="uq_review_outputs_run_type_key_version",
        ),
        UniqueConstraint(
            "review_run_id", "idempotency_key", name="uq_review_outputs_run_idempotency"
        ),
    )


class HumanInputRequestORM(Base):
    """版本化大纲人工输入请求。"""

    __tablename__ = "human_input_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.run_id"), index=True, nullable=False
    )
    request_version: Mapped[int] = mapped_column(nullable=False)
    outline_output_id: Mapped[str] = mapped_column(
        ForeignKey("review_outputs.output_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed_actions: Mapped[list] = mapped_column(JSONB, nullable=False)
    resolved_input_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "human_inputs.human_input_id",
            use_alter=True,
            name="fk_human_input_requests_resolved_input",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("request_version >= 1", name="ck_human_input_requests_version"),
        CheckConstraint(
            "status IN ('open','resolved','cancelled')",
            name="ck_human_input_requests_status",
        ),
        UniqueConstraint(
            "review_run_id", "request_version", name="uq_human_input_requests_run_version"
        ),
        UniqueConstraint(
            "request_id", "request_version", name="uq_human_input_requests_id_version"
        ),
        Index(
            "uq_human_input_requests_one_open_per_run",
            "review_run_id",
            unique=True,
            postgresql_where=(status == "open"),
        ),
    )


class HumanInputORM(Base):
    """同一请求至多一条的不可变用户输入。"""

    __tablename__ = "human_inputs"

    human_input_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_version: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id", "request_version"],
            ["human_input_requests.request_id", "human_input_requests.request_version"],
            name="fk_human_inputs_request_version",
        ),
        CheckConstraint("action IN ('approve','edit','feedback')", name="ck_human_inputs_action"),
        UniqueConstraint("request_id", name="uq_human_inputs_request"),
        UniqueConstraint(
            "submitted_by", "idempotency_key", name="uq_human_inputs_submitter_idempotency"
        ),
    )


class ArtifactORM(Base):
    """Artifact Storage 中持久文件的业务元数据。"""

    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.run_id"), index=True, nullable=False
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_output_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_outputs.output_id"), nullable=True
    )
    artifact_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_nonnegative"),
        CheckConstraint(
            "artifact_type IN ('review_markdown','search_strategy','source_manifest',"
            "'evidence_matrix','bibliography','run_summary')",
            name="ck_artifacts_type",
        ),
        UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
        UniqueConstraint("review_run_id", "idempotency_key", name="uq_artifacts_run_idempotency"),
    )
