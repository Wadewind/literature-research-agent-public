"""SQLAlchemy ORM 模型。"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
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
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
