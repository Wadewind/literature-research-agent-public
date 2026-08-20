"""个人文献库、Project 收录与 PDF 预览 API 测试。"""

from dataclasses import replace

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.papers import (
    get_paper_query_service,
    get_paper_service,
    get_project_library_service,
)
from literature_agent.application.paper_query_service import PaperQueryService
from literature_agent.application.paper_service import PaperService
from literature_agent.application.project_library_service import ProjectLibraryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.main import create_app
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_storage import FakeStorage

_PDF_CONTENT = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n"


class _Fixture:
    """共享 Fake 仓库与应用服务。"""

    def __init__(self) -> None:
        self.project_repo = FakeProjectRepository()
        self.paper_repo = FakePaperRepository()
        self.version_repo = FakePaperVersionRepository()
        self.relation_repo = FakeProjectPaperRepository()
        self.storage = FakeStorage()

    def query_service(self) -> PaperQueryService:
        return PaperQueryService(
            session_factory=fake_session,
            project_repo_factory=lambda _session: self.project_repo,
            paper_repo_factory=lambda _session: self.paper_repo,
            paper_version_repo_factory=lambda _session: self.version_repo,
            project_paper_repo_factory=lambda _session: self.relation_repo,
            storage=self.storage,
        )

    def library_service(self) -> ProjectLibraryService:
        return ProjectLibraryService(
            session_factory=fake_session,
            project_repo_factory=lambda _session: self.project_repo,
            paper_repo_factory=lambda _session: self.paper_repo,
            paper_version_repo_factory=lambda _session: self.version_repo,
            project_paper_repo_factory=lambda _session: self.relation_repo,
        )

    def paper_service(self) -> PaperService:
        return PaperService(
            session_factory=fake_session,
            paper_repo_factory=lambda _session: self.paper_repo,
        )


@pytest_asyncio.fixture
async def fixture():
    """准备一个 owner、两个 Project 和一篇已解析 Paper。"""
    fx = _Fixture()
    project = create_project(owner_id="user-1", name="项目一", description="")
    other = create_project(owner_id="user-1", name="项目二", description="")
    await fx.project_repo.add(project)
    await fx.project_repo.add(other)
    paper = create_paper(owner_id="user-1")
    await fx.paper_repo.add(paper)
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="abc123",
        storage_key="user-1/papers/paper.pdf",
        size_bytes=len(_PDF_CONTENT),
        content_type="application/pdf",
        display_filename="attention.pdf",
    )
    version = replace(version, current_parse_revision_id="revision-1")
    await fx.version_repo.add(version)
    await fx.relation_repo.add(
        create_project_paper(project.project_id, paper.paper_id, version.version_id)
    )
    await fx.storage.write(version.storage_key, _PDF_CONTENT)

    app = create_app()

    async def actor_override() -> ActorContext:
        return ActorContext(owner_id="user-1")

    async def query_service_override() -> PaperQueryService:
        return fx.query_service()

    async def library_service_override() -> ProjectLibraryService:
        return fx.library_service()

    async def paper_service_override() -> PaperService:
        return fx.paper_service()

    app.dependency_overrides[get_actor] = actor_override
    app.dependency_overrides[get_paper_query_service] = query_service_override
    app.dependency_overrides[get_project_library_service] = library_service_override
    app.dependency_overrides[get_paper_service] = paper_service_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield fx, test_client, project, other, paper, version

    app.dependency_overrides.clear()


async def test_project_and_personal_library_lists(fixture) -> None:
    """Project 列表只显示已收录项，个人文献库显示完整收录范围。"""
    _, client, project, other, paper, version = fixture

    project_response = await client.get(f"/api/v1/projects/{project.project_id}/papers")
    other_response = await client.get(f"/api/v1/projects/{other.project_id}/papers")
    library_response = await client.get("/api/v1/library/papers")

    assert project_response.status_code == 200
    assert other_response.json() == []
    item = project_response.json()[0]
    assert item["paper_id"] == paper.paper_id
    assert item["version"]["version_id"] == version.version_id
    assert item["version"]["parse_ready"] is True
    assert library_response.json()[0]["project_ids"] == [project.project_id]


async def test_add_existing_and_remove_membership(fixture) -> None:
    """已有 Paper 可无上传加入另一个 Project，移除后仍保留在个人文献库。"""
    _, client, project, other, paper, version = fixture

    added = await client.post(
        f"/api/v1/projects/{other.project_id}/papers",
        json={"paper_id": paper.paper_id, "version_id": version.version_id},
    )
    removed = await client.delete(f"/api/v1/projects/{project.project_id}/papers/{paper.paper_id}")
    library = await client.get("/api/v1/library/papers")

    assert added.status_code == 201
    assert added.json()["already_added"] is False
    assert removed.status_code == 204
    assert library.json()[0]["project_ids"] == [other.project_id]


async def test_get_version_file_requires_project_membership(fixture) -> None:
    """PDF 只能通过已固定该 Version 的 Project 访问。"""
    _, client, project, other, _, version = fixture

    allowed = await client.get(
        f"/api/v1/projects/{project.project_id}/paper-versions/{version.version_id}/file"
    )
    denied = await client.get(
        f"/api/v1/projects/{other.project_id}/paper-versions/{version.version_id}/file"
    )

    assert allowed.status_code == 200
    assert allowed.content == _PDF_CONTENT
    assert denied.status_code == 404


async def test_archive_and_restore_library_paper(fixture) -> None:
    """个人文献库 Paper 归档与恢复均返回 200 且幂等。"""
    fx, client, _, _, paper, _ = fixture

    archived = await client.post(f"/api/v1/library/papers/{paper.paper_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["paper_id"] == paper.paper_id
    assert archived.json()["archived_at"] is not None

    again = await client.post(f"/api/v1/library/papers/{paper.paper_id}/archive")
    assert again.status_code == 200
    assert again.json()["archived_at"] == archived.json()["archived_at"]

    restored = await client.post(f"/api/v1/library/papers/{paper.paper_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None

    restore_again = await client.post(f"/api/v1/library/papers/{paper.paper_id}/restore")
    assert restore_again.status_code == 200


async def test_archive_paper_not_found_or_forbidden_returns_404(fixture) -> None:
    """不存在或越权的 Paper 归档/恢复返回 404。"""
    _, client, _, _, _, _ = fixture

    assert (await client.post("/api/v1/library/papers/missing/archive")).status_code == 404
    assert (await client.post("/api/v1/library/papers/missing/restore")).status_code == 404


async def test_library_list_excludes_archived_by_default(fixture) -> None:
    """个人文献库默认隐藏已归档 Paper，include_archived=true 时返回。"""
    fx, client, _, _, paper, _ = fixture

    default = await client.get("/api/v1/library/papers")
    assert [item["paper_id"] for item in default.json()] == [paper.paper_id]

    await client.post(f"/api/v1/library/papers/{paper.paper_id}/archive")

    hidden = await client.get("/api/v1/library/papers")
    full = await client.get("/api/v1/library/papers", params={"include_archived": "true"})

    assert hidden.json() == []
    assert len(full.json()) == 1
    assert full.json()[0]["archived_at"] is not None


async def test_add_archived_paper_to_project_returns_409(fixture) -> None:
    """收录已归档 Paper 到 Project 返回 409 paper_archived。"""
    fx, client, _, other, paper, version = fixture
    await fx.paper_repo.update(paper.archive())

    response = await client.post(
        f"/api/v1/projects/{other.project_id}/papers",
        json={"paper_id": paper.paper_id, "version_id": version.version_id},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "paper_archived"


async def test_add_paper_to_archived_project_returns_409(fixture) -> None:
    """向已归档 Project 收录 Paper 返回 409 project_archived。"""
    fx, client, _, other, paper, version = fixture
    await fx.project_repo.update(other.archive())

    response = await client.post(
        f"/api/v1/projects/{other.project_id}/papers",
        json={"paper_id": paper.paper_id, "version_id": version.version_id},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "project_archived"


async def test_remove_paper_from_archived_project_returns_409(fixture) -> None:
    """已归档 Project 拒绝移除收录，返回 409 project_archived。"""
    fx, client, project, _, paper, _ = fixture
    await fx.project_repo.update(project.archive())

    response = await client.delete(
        f"/api/v1/projects/{project.project_id}/papers/{paper.paper_id}"
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "project_archived"


async def test_project_paper_list_keeps_archived_paper(fixture) -> None:
    """Paper 归档不影响已有 Project 收录关系，列表仍可读。"""
    fx, client, project, _, paper, _ = fixture
    await fx.paper_repo.update(paper.archive())

    response = await client.get(f"/api/v1/projects/{project.project_id}/papers")

    assert response.status_code == 200
    assert [item["paper_id"] for item in response.json()] == [paper.paper_id]
