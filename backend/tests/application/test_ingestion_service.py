"""Ingestion Application Service 测试。"""

import pytest
import pytest_asyncio

from literature_agent.application.ingestion_service import IngestionService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    FileValidationError,
    IdempotencyConflictError,
    ProjectNotFoundError,
)
from literature_agent.domain.project import create_project
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_idempotency_repository import FakeIdempotencyRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_run_repository import FakeRunRepository
from tests.fakes.fake_storage import FakeStorage


@pytest.fixture
def project_repo() -> FakeProjectRepository:
    """提供 Fake Project Repository。"""
    return FakeProjectRepository()


@pytest.fixture
def paper_repo() -> FakePaperRepository:
    """提供 Fake Paper Repository。"""
    return FakePaperRepository()


@pytest.fixture
def paper_version_repo() -> FakePaperVersionRepository:
    """提供 Fake PaperVersion Repository。"""
    return FakePaperVersionRepository()


@pytest.fixture
def idempotency_repo() -> FakeIdempotencyRepository:
    """提供 Fake Idempotency Repository。"""
    return FakeIdempotencyRepository()


@pytest.fixture
def run_repo() -> FakeRunRepository:
    """提供 Fake Run Repository。"""
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    """提供 Fake Event Repository。"""
    return FakeEventRepository()


@pytest.fixture
def outbox_repo() -> FakeOutboxRepository:
    """提供 Fake Outbox Repository。"""
    return FakeOutboxRepository()


@pytest.fixture
def storage() -> FakeStorage:
    """提供 Fake Storage。"""
    return FakeStorage()


@pytest_asyncio.fixture
async def service(
    project_repo: FakeProjectRepository,
    paper_repo: FakePaperRepository,
    paper_version_repo: FakePaperVersionRepository,
    idempotency_repo: FakeIdempotencyRepository,
    run_repo: FakeRunRepository,
    event_repo: FakeEventRepository,
    outbox_repo: FakeOutboxRepository,
    storage: FakeStorage,
) -> IngestionService:
    """提供使用 Fake Repository 的 IngestionService。"""
    return IngestionService(
        max_upload_size_bytes=1024 * 1024,
        session_factory=fake_session,
        project_repo_factory=lambda _session: project_repo,
        paper_repo_factory=lambda _session: paper_repo,
        paper_version_repo_factory=lambda _session: paper_version_repo,
        idempotency_repo_factory=lambda _session: idempotency_repo,
        run_repo_factory=lambda _session: run_repo,
        event_repo_factory=lambda _session: event_repo,
        outbox_repo_factory=lambda _session: outbox_repo,
        storage=storage,
    )


@pytest.fixture
def actor() -> ActorContext:
    """提供测试 Actor。"""
    return ActorContext(owner_id="user-1")


@pytest_asyncio.fixture
async def project(
    actor: ActorContext,
    project_repo: FakeProjectRepository,
) -> object:
    """提供已存入 Fake Repository 的测试 Project。"""
    p = create_project(actor.owner_id, "测试项目", "")
    await project_repo.add(p)
    return p


def _pdf_content() -> bytes:
    """返回最小合法 PDF 字节。"""
    return b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n"


@pytest.mark.asyncio
async def test_upload_valid_pdf_creates_run(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    paper_version_repo: FakePaperVersionRepository,
) -> None:
    """合法 PDF 上传应创建 Paper、Version、Run 和 Event。"""
    content = _pdf_content()

    result = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="test.pdf",
        content_type="application/pdf",
        content=content,
        idempotency_key="key-1",
        correlation_id="corr-1",
    )

    assert result.status == "queued"
    assert result.paper_id
    assert result.version_id
    assert result.run_id

    version = await paper_version_repo.get_by_id(result.version_id)
    assert version is not None
    assert version.display_filename == "test.pdf"


@pytest.mark.asyncio
async def test_upload_creates_outbox_entry_in_same_flow(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    outbox_repo: FakeOutboxRepository,
) -> None:
    """上传成功应同时创建一条 PENDING 的 Outbox 记录。"""
    result = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="test.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="key-outbox",
        correlation_id="corr-1",
    )

    entry = await outbox_repo.get_by_run_id(result.run_id)
    assert entry is not None
    assert entry.status.value == "pending"
    assert entry.attempt_count == 0


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(
    service: IngestionService,
    actor: ActorContext,
    project: object,
) -> None:
    """非 PDF 文件应被校验拒绝。"""
    with pytest.raises(FileValidationError, match="仅接受 PDF"):
        await service.upload_paper_file(
            actor=actor,
            project_id=project.project_id,
            filename="test.txt",
            content_type="text/plain",
            content=b"not a pdf",
            idempotency_key="key-2",
            correlation_id="corr-1",
        )


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(
    service: IngestionService,
    actor: ActorContext,
    project: object,
) -> None:
    """超过大小限制的文件应被拒绝。"""
    oversized = b"%PDF-" + b"x" * (1024 * 1024 + 1)
    with pytest.raises(FileValidationError, match="大小超过限制"):
        await service.upload_paper_file(
            actor=actor,
            project_id=project.project_id,
            filename="big.pdf",
            content_type="application/pdf",
            content=oversized,
            idempotency_key="key-3",
            correlation_id="corr-1",
        )


@pytest.mark.asyncio
async def test_upload_rejects_missing_idempotency_key(
    service: IngestionService,
    actor: ActorContext,
    project: object,
) -> None:
    """缺少 Idempotency-Key 应被拒绝。"""
    with pytest.raises(FileValidationError, match="Idempotency-Key"):
        await service.upload_paper_file(
            actor=actor,
            project_id=project.project_id,
            filename="test.pdf",
            content_type="application/pdf",
            content=_pdf_content(),
            idempotency_key="",
            correlation_id="corr-1",
        )


@pytest.mark.asyncio
async def test_upload_rejects_unknown_project(
    service: IngestionService,
    actor: ActorContext,
) -> None:
    """上传到过不存在的 Project 应抛 ProjectNotFoundError。"""
    with pytest.raises(ProjectNotFoundError):
        await service.upload_paper_file(
            actor=actor,
            project_id="00000000-0000-0000-0000-000000000000",
            filename="test.pdf",
            content_type="application/pdf",
            content=_pdf_content(),
            idempotency_key="key-4",
            correlation_id="corr-1",
        )


@pytest.mark.asyncio
async def test_upload_idempotent_same_request_returns_same_run(
    service: IngestionService,
    actor: ActorContext,
    project: object,
) -> None:
    """相同 Idempotency-Key 和相同请求应返回同一 run_id。"""
    content = _pdf_content()

    result1 = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="test.pdf",
        content_type="application/pdf",
        content=content,
        idempotency_key="key-5",
        correlation_id="corr-1",
    )
    result2 = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="test.pdf",
        content_type="application/pdf",
        content=content,
        idempotency_key="key-5",
        correlation_id="corr-2",
    )

    assert result1.run_id == result2.run_id
    assert result1.paper_id == result2.paper_id
    assert result1.version_id == result2.version_id


@pytest.mark.asyncio
async def test_upload_idempotent_different_request_raises_conflict(
    service: IngestionService,
    actor: ActorContext,
    project: object,
) -> None:
    """相同 Idempotency-Key 但不同请求应抛冲突。"""
    await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="test.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="key-6",
        correlation_id="corr-1",
    )
    with pytest.raises(IdempotencyConflictError):
        await service.upload_paper_file(
            actor=actor,
            project_id=project.project_id,
            filename="other.pdf",
            content_type="application/pdf",
            content=_pdf_content(),
            idempotency_key="key-6",
            correlation_id="corr-2",
        )
