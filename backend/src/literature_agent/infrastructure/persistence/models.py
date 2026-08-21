"""SQLAlchemy ORM 模型。"""

from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import Computed


class Base(DeclarativeBase):
    """ORM 基类。"""


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
    """ClaimSet 的持久化映射（一个 rag_answer Run 至多一个）。"""

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
