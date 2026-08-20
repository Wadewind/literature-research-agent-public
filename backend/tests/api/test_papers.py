"""Paper 列表与 PDF 文件预览 API 路由测试（切片 10）。"""

import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.papers import get_paper_query_service
from literature_agent.application.paper_query_service import PaperQueryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.project import create_project
from literature_agent.main import create_app
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_storage import FakeStorage

_PDF_CONTENT = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n"


class _Fixture:
    """测试上下文：仓库、存储与已准备的 Project/Paper/Version。"""

    def __init__(self) -> None:
        self.project_repo = FakeProjectRepository()
        self.paper_repo = FakePaperRepository()
        self.version_repo = FakePaperVersionRepository()
        self.storage = FakeStorage()

    def build_service(self) -> PaperQueryService:
        """基于 Fake 依赖构建 PaperQueryService。"""
        return PaperQueryService(
            session_factory=fake_session,
            project_repo_factory=lambda _session: self.project_repo,
            paper_repo_factory=lambda _session: self.paper_repo,
            paper_version_repo_factory=lambda _session: self.version_repo,
            storage=self.storage,
        )


@pytest_asyncio.fixture
async def fixture():
    """准备含一个 Project、Paper 和 Version 的测试上下文。"""
    fx = _Fixture()
    project = create_project(owner_id="user-1", name="测试项目", description="")
    await fx.project_repo.add(project)
    paper = create_paper(owner_id="user-1", project_id=project.project_id)
    await fx.paper_repo.add(paper)
    version = create_paper_version(
        paper_id=paper.paper_id,
        file_hash="abc123",
        storage_key="user-1/project/paper/paper.pdf",
        size_bytes=len(_PDF_CONTENT),
        content_type="application/pdf",
        display_filename="attention.pdf",
    )
    await fx.version_repo.add(version)
    await fx.storage.write(version.storage_key, _PDF_CONTENT)

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-1")
    app.dependency_overrides[get_paper_query_service] = fx.build_service

    with TestClient(app) as test_client:
        yield fx, test_client, project.project_id, paper.paper_id, version.version_id

    app.dependency_overrides.clear()


def test_list_papers_returns_latest_version_summary(fixture) -> None:
    """Paper 列表应包含最新 Version 摘要。"""
    _, test_client, project_id, paper_id, version_id = fixture

    response = test_client.get(f"/api/v1/projects/{project_id}/papers")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["paper_id"] == paper_id
    latest = data[0]["latest_version"]
    assert latest["version_id"] == version_id
    assert latest["display_filename"] == "attention.pdf"
    assert latest["size_bytes"] == len(_PDF_CONTENT)
    assert latest["parse_ready"] is False


async def test_list_papers_parse_ready_after_revision(fixture) -> None:
    """设置当前 Parse Revision 后 parse_ready 应为 true。"""
    fx, test_client, project_id, _, version_id = fixture
    await fx.version_repo.set_current_parse_revision(version_id, "revision-1")

    response = test_client.get(f"/api/v1/projects/{project_id}/papers")

    assert response.status_code == 200
    assert response.json()[0]["latest_version"]["parse_ready"] is True


def test_list_papers_unknown_project_returns_404(fixture) -> None:
    """不存在或不属于当前 actor 的 Project 应返回 404。"""
    _, test_client, _, _, _ = fixture

    response = test_client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000/papers")

    assert response.status_code == 404


def test_get_version_file_returns_pdf_bytes(fixture) -> None:
    """file 端点应返回 PDF 字节与 inline 预览头（无需 Parse Revision）。"""
    _, test_client, project_id, _, version_id = fixture

    response = test_client.get(f"/api/v1/projects/{project_id}/paper-versions/{version_id}/file")

    assert response.status_code == 200
    assert response.content == _PDF_CONTENT
    assert response.headers["content-type"] == "application/pdf"
    assert "inline" in response.headers["content-disposition"]
    assert "attention.pdf" in response.headers["content-disposition"]


def test_get_version_file_unknown_version_returns_404(fixture) -> None:
    """不存在的 Version 应返回 404。"""
    _, test_client, project_id, _, _ = fixture

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/00000000-0000-0000-0000-000000000000/file"
    )

    assert response.status_code == 404


def test_cross_owner_access_returns_404(fixture) -> None:
    """其他用户访问 Project 的 Paper 列表与文件应返回 404。"""
    fx, _, project_id, _, version_id = fixture

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-2")
    app.dependency_overrides[get_paper_query_service] = fx.build_service

    with TestClient(app) as other_client:
        list_response = other_client.get(f"/api/v1/projects/{project_id}/papers")
        file_response = other_client.get(
            f"/api/v1/projects/{project_id}/paper-versions/{version_id}/file"
        )

    assert list_response.status_code == 404
    assert file_response.status_code == 404
