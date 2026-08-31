"""Project 文献库 arXiv 搜索与引入 API 测试。"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Response
from httpx import ASGITransport, AsyncClient

from literature_agent.api.dependencies import get_actor, get_correlation_id
from literature_agent.api.project_arxiv import (
    ImportArxivPaperRequest,
    get_project_arxiv_library_service,
    import_arxiv_paper,
)
from literature_agent.api.project_arxiv import (
    router as project_arxiv_router,
)
from literature_agent.application.ingestion_service import UploadResult
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.arxiv import ArxivPaper
from literature_agent.main import create_app


class _Service:
    def __init__(self) -> None:
        self.imported: list[str] = []

    async def search(self, **_kwargs: object) -> list[ArxivPaper]:
        now = datetime(2026, 8, 31, tzinfo=UTC)
        return [
            ArxivPaper(
                arxiv_id="2405.15460",
                arxiv_version="v1",
                title="TD3 Based Collision Free Motion Planning",
                abstract="Abstract",
                authors=("Alice", "Bob"),
                categories=("cs.RO",),
                published_at=now,
                updated_at=now,
                pdf_url="https://arxiv.org/pdf/2405.15460v1",
            )
        ]

    async def import_paper(self, **kwargs: object) -> UploadResult:
        self.imported.append(str(kwargs["versioned_arxiv_id"]))
        return UploadResult(
            run_id="run-1",
            paper_id="paper-1",
            version_id="version-1",
            status="queued",
            reused=False,
            already_added=False,
        )


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(project_arxiv_router)
    service = _Service()

    async def actor_override() -> ActorContext:
        return ActorContext(owner_id="user-1")

    async def service_override() -> _Service:
        return service

    app.dependency_overrides[get_actor] = actor_override
    app.dependency_overrides[get_correlation_id] = lambda: "project-arxiv-test"
    app.dependency_overrides[get_project_arxiv_library_service] = service_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client, service
    app.dependency_overrides.clear()


async def test_search_returns_public_metadata_without_pdf_url(client) -> None:
    test_client, _ = client
    response = await test_client.get(
        "/api/v1/projects/project-1/arxiv/search",
        params={"q": "all:path planning", "max_results": 8},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "arxiv_id": "2405.15460",
            "arxiv_version": "v1",
            "versioned_id": "2405.15460v1",
            "title": "TD3 Based Collision Free Motion Planning",
            "abstract": "Abstract",
            "authors": ["Alice", "Bob"],
            "categories": ["cs.RO"],
            "published_at": "2026-08-31T00:00:00Z",
            "updated_at": "2026-08-31T00:00:00Z",
            "page_count": None,
        }
    ]


async def test_import_requires_idempotency_key(client) -> None:
    _, service = client
    with pytest.raises(HTTPException) as raised:
        await import_arxiv_paper(
            project_id="project-1",
            payload=ImportArxivPaperRequest(versioned_arxiv_id="2405.15460v1"),
            actor=ActorContext(owner_id="user-1"),
            service=service,
            response=Response(),
            correlation_id="project-arxiv-test",
            idempotency_key=None,
        )
    assert raised.value.status_code == 400


async def test_import_selected_version_uses_service(client) -> None:
    _, service = client
    result = await import_arxiv_paper(
        project_id="project-1",
        payload=ImportArxivPaperRequest(versioned_arxiv_id="2405.15460v1"),
        actor=ActorContext(owner_id="user-1"),
        service=service,
        response=Response(),
        correlation_id="project-arxiv-test",
        idempotency_key="arxiv-import-1",
    )

    assert result.status == "queued"
    assert service.imported == ["2405.15460v1"]


def test_application_registers_project_arxiv_routes() -> None:
    app = create_app()
    paths = set(app.openapi()["paths"])

    assert "/api/v1/projects/{project_id}/arxiv/search" in paths
    assert "/api/v1/projects/{project_id}/arxiv/import" in paths
