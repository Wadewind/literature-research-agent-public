from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from literature_agent.application.project_arxiv_library_service import (
    ProjectArxivLibraryService,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.arxiv import ArxivPaper, ArxivSearchQuery, DownloadedPdf
from literature_agent.domain.exceptions import ProjectArchivedError, ProjectNotFoundError
from literature_agent.domain.project import create_project
from tests.fakes.fake_project_repository import FakeProjectRepository


class _Gateway:
    def __init__(self) -> None:
        now = datetime(2026, 8, 31, tzinfo=UTC)
        self.paper = ArxivPaper(
            arxiv_id="2405.15460",
            arxiv_version="v1",
            title="TD3 Based Collision Free Motion Planning",
            abstract="Abstract",
            authors=("Alice",),
            categories=("cs.RO",),
            published_at=now,
            updated_at=now,
            pdf_url="https://arxiv.org/pdf/2405.15460v1",
        )
        self.search_calls: list[ArxivSearchQuery] = []
        self.download_calls: list[tuple[str, int]] = []

    async def search(self, query: ArxivSearchQuery) -> list[ArxivPaper]:
        self.search_calls.append(query)
        return [self.paper]

    async def download_pdf(self, url: str, *, remaining_budget_bytes: int) -> DownloadedPdf:
        self.download_calls.append((url, remaining_budget_bytes))
        return DownloadedPdf.from_content(b"%PDF-1.4\nfixture", "application/pdf")


class _Ingestion:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def upload_paper_file(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"status": "queued"}


async def _fixture(*, archived: bool = False):
    repositories = FakeProjectRepository()
    project = create_project("owner-1", "Research", "")
    if archived:
        project = project.archive()
    await repositories.add(project)

    @asynccontextmanager
    async def session_factory():
        yield object()

    gateway = _Gateway()
    ingestion = _Ingestion()
    service = ProjectArxivLibraryService(
        session_factory=session_factory,
        project_repo_factory=lambda _: repositories,
        arxiv_gateway=gateway,
        ingestion_service=ingestion,
        max_download_bytes=1024,
    )
    return project, gateway, ingestion, service


@pytest.mark.asyncio
async def test_search_authorizes_project_before_external_io() -> None:
    project, gateway, _, service = await _fixture()

    with pytest.raises(ProjectNotFoundError):
        await service.search(
            actor=ActorContext(owner_id="owner-2"),
            project_id=project.project_id,
            query=ArxivSearchQuery("all:path planning"),
        )

    assert gateway.search_calls == []


@pytest.mark.asyncio
async def test_archived_project_rejects_search_before_external_io() -> None:
    project, gateway, _, service = await _fixture(archived=True)

    with pytest.raises(ProjectArchivedError):
        await service.search(
            actor=ActorContext(owner_id="owner-1"),
            project_id=project.project_id,
            query=ArxivSearchQuery("all:path planning"),
        )

    assert gateway.search_calls == []


@pytest.mark.asyncio
async def test_import_uses_validated_official_url_and_existing_ingestion_pipeline() -> None:
    project, gateway, ingestion, service = await _fixture()

    result = await service.import_paper(
        actor=ActorContext(owner_id="owner-1"),
        project_id=project.project_id,
        versioned_arxiv_id="2405.15460v1",
        idempotency_key="import-1",
        correlation_id="corr-1",
    )

    assert result == {"status": "queued"}
    assert gateway.download_calls == [("https://arxiv.org/pdf/2405.15460v1", 1024)]
    assert ingestion.calls[0]["filename"] == "arxiv-2405.15460v1.pdf"
    assert ingestion.calls[0]["content"] == b"%PDF-1.4\nfixture"
