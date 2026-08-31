"""Ingestion Application Service 测试。"""

import pytest
import pytest_asyncio

from literature_agent.application.ingestion_service import IngestionService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    FileValidationError,
    IdempotencyConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from literature_agent.domain.paper import PaperTitleSource
from literature_agent.domain.project import create_project
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_idempotency_repository import FakeIdempotencyRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
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
def project_paper_repo() -> FakeProjectPaperRepository:
    """提供 Fake ProjectPaper Repository。"""
    return FakeProjectPaperRepository()


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
    project_paper_repo: FakeProjectPaperRepository,
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
        project_paper_repo_factory=lambda _session: project_paper_repo,
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
async def test_upload_with_arxiv_metadata_sets_paper_title(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    paper_repo: FakePaperRepository,
) -> None:
    """可信 arXiv 元数据应在异步解析前成为 Paper 标题。"""
    result = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="arxiv-2405.15460v1.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="arxiv-title",
        correlation_id="corr-arxiv-title",
        paper_title="TD3 Based Collision Free Motion Planning",
        paper_title_source=PaperTitleSource.ARXIV_METADATA,
    )

    paper = await paper_repo.get_by_id(result.paper_id)
    assert paper is not None
    assert paper.title == "TD3 Based Collision Free Motion Planning"
    assert paper.title_source is PaperTitleSource.ARXIV_METADATA


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


@pytest.mark.asyncio
async def test_same_owner_hash_reuses_version_across_projects(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    project_repo: FakeProjectRepository,
    project_paper_repo: FakeProjectPaperRepository,
) -> None:
    """同一 owner 的相同 PDF 应复用 Version，并加入另一个 Project。"""
    other = create_project(actor.owner_id, "另一个项目", "")
    await project_repo.add(other)

    first = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="paper.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="hash-first",
        correlation_id="corr-1",
    )
    reused = await service.upload_paper_file(
        actor=actor,
        project_id=other.project_id,
        filename="paper.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="hash-second",
        correlation_id="corr-2",
    )

    assert reused.reused is True
    assert reused.already_added is False
    assert reused.paper_id == first.paper_id
    assert reused.version_id == first.version_id
    assert reused.run_id == first.run_id
    assert await project_paper_repo.get(other.project_id, first.paper_id) is not None


@pytest.mark.asyncio
async def test_reused_hash_backfills_arxiv_metadata_title(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    project_repo: FakeProjectRepository,
    paper_repo: FakePaperRepository,
) -> None:
    """再次按 arXiv 引入已有文件时应修复缺失的正式标题。"""
    other = create_project(actor.owner_id, "另一个项目", "")
    await project_repo.add(other)
    first = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="arxiv-2405.15460v1.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="arxiv-title-first",
        correlation_id="corr-1",
    )

    reused = await service.upload_paper_file(
        actor=actor,
        project_id=other.project_id,
        filename="arxiv-2405.15460v1.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="arxiv-title-second",
        correlation_id="corr-2",
        paper_title="TD3 Based Collision Free Motion Planning",
        paper_title_source=PaperTitleSource.ARXIV_METADATA,
    )

    paper = await paper_repo.get_by_id(first.paper_id)
    assert reused.reused is True
    assert paper is not None
    assert paper.title == "TD3 Based Collision Free Motion Planning"
    assert paper.title_source is PaperTitleSource.ARXIV_METADATA


@pytest.mark.asyncio
async def test_ready_version_reuse_has_no_new_run(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    project_repo: FakeProjectRepository,
    paper_version_repo: FakePaperVersionRepository,
) -> None:
    """复用已经解析完成的 Version 时不应暴露新的 Run。"""
    other = create_project(actor.owner_id, "另一个项目", "")
    await project_repo.add(other)
    first = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="paper.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="ready-first",
        correlation_id="corr-1",
    )
    await paper_version_repo.set_current_parse_revision(first.version_id, "revision-1")

    reused = await service.upload_paper_file(
        actor=actor,
        project_id=other.project_id,
        filename="renamed.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="ready-second",
        correlation_id="corr-2",
    )

    assert reused.reused is True
    assert reused.run_id is None
    assert reused.status == "ready"


@pytest.mark.asyncio
async def test_upload_to_archived_project_rejected(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    project_repo: FakeProjectRepository,
) -> None:
    """已归档 Project 拒绝新上传。"""
    await project_repo.update(project.archive())

    with pytest.raises(ProjectArchivedError):
        await service.upload_paper_file(
            actor=actor,
            project_id=project.project_id,
            filename="test.pdf",
            content_type="application/pdf",
            content=_pdf_content(),
            idempotency_key="archived-project",
            correlation_id="corr-1",
        )


@pytest.mark.asyncio
async def test_reuse_archived_paper_returns_flag_without_restoring(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    project_repo: FakeProjectRepository,
    paper_repo: FakePaperRepository,
) -> None:
    """同哈希上传命中已归档 Paper：正常复用并提示，不自动恢复归档。"""
    other = create_project(actor.owner_id, "另一个项目", "")
    await project_repo.add(other)
    first = await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="paper.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="archived-first",
        correlation_id="corr-1",
    )
    paper = await paper_repo.get_by_id(first.paper_id)
    assert paper is not None
    await paper_repo.update(paper.archive())

    reused = await service.upload_paper_file(
        actor=actor,
        project_id=other.project_id,
        filename="paper.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="archived-second",
        correlation_id="corr-2",
    )

    assert reused.reused is True
    assert reused.paper_archived is True
    assert reused.paper_id == first.paper_id
    # 复用不自动恢复归档
    still = await paper_repo.get_by_id(first.paper_id)
    assert still is not None and still.is_archived is True


@pytest.mark.asyncio
async def test_reuse_active_paper_has_no_archived_flag(
    service: IngestionService,
    actor: ActorContext,
    project: object,
    project_repo: FakeProjectRepository,
) -> None:
    """复用未归档 Paper 时 paper_archived 为 False。"""
    other = create_project(actor.owner_id, "另一个项目", "")
    await project_repo.add(other)
    await service.upload_paper_file(
        actor=actor,
        project_id=project.project_id,
        filename="paper.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="active-first",
        correlation_id="corr-1",
    )

    reused = await service.upload_paper_file(
        actor=actor,
        project_id=other.project_id,
        filename="paper.pdf",
        content_type="application/pdf",
        content=_pdf_content(),
        idempotency_key="active-second",
        correlation_id="corr-2",
    )

    assert reused.reused is True
    assert reused.paper_archived is False
