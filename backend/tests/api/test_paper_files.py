"""Paper 上传 API 路由测试。"""

from io import BytesIO

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.paper_files import get_ingestion_service
from literature_agent.application.ingestion_service import IngestionService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.project import create_project
from literature_agent.main import create_app
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_idempotency_repository import FakeIdempotencyRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_run_repository import FakeRunRepository
from tests.fakes.fake_storage import FakeStorage


def _build_fake_service(
    project_repo: FakeProjectRepository,
) -> tuple[IngestionService, FakePaperRepository]:
    """基于 Fake Repository 构建 IngestionService，并返回 Paper 仓库便于测试准备。"""
    paper_repo = FakePaperRepository()
    paper_version_repo = FakePaperVersionRepository()
    idempotency_repo = FakeIdempotencyRepository()
    outbox_repo = FakeOutboxRepository()
    run_repo = FakeRunRepository()
    event_repo = FakeEventRepository()
    storage = FakeStorage()
    project_paper_repo = FakeProjectPaperRepository()

    return (
        IngestionService(
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
        ),
        paper_repo,
    )


@pytest_asyncio.fixture
async def client():
    """提供已注入 Fake 依赖的 TestClient。"""
    project_repo = FakeProjectRepository()
    project = create_project(owner_id="user-1", name="测试项目", description="")
    await project_repo.add(project)

    service, paper_repo = _build_fake_service(project_repo)

    app = create_app()

    async def actor_override() -> ActorContext:
        return ActorContext(owner_id="user-1")

    async def service_override() -> IngestionService:
        return service

    app.dependency_overrides[get_actor] = actor_override
    app.dependency_overrides[get_ingestion_service] = service_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client, project.project_id, project_repo, paper_repo

    app.dependency_overrides.clear()


def _pdf_file() -> tuple[BytesIO, str]:
    """构造最小 PDF 上传文件。"""
    content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n"
    return BytesIO(content), "test.pdf"


async def test_upload_paper_file_returns_202(client) -> None:
    """上传合法 PDF 应返回 202 和 run_id。"""
    test_client, project_id, *_ = client
    file_obj, filename = _pdf_file()

    response = await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        headers={"Idempotency-Key": "api-key-1"},
        files={"file": (filename, file_obj, "application/pdf")},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["run_id"]
    assert data["paper_id"]
    assert data["version_id"]


async def test_upload_missing_idempotency_key_returns_400(client) -> None:
    """缺少 Idempotency-Key 应返回 400。"""
    test_client, project_id, *_ = client
    file_obj, filename = _pdf_file()

    response = await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        files={"file": (filename, file_obj, "application/pdf")},
    )

    assert response.status_code == 400


async def test_upload_non_pdf_returns_400(client) -> None:
    """非 PDF 文件应返回 400。"""
    test_client, project_id, *_ = client

    response = await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        headers={"Idempotency-Key": "api-key-2"},
        files={"file": ("test.txt", BytesIO(b"not pdf"), "text/plain")},
    )

    assert response.status_code == 400


async def test_upload_unknown_project_returns_404(client) -> None:
    """上传到不存在 Project 应返回 404。"""
    test_client, *_ = client
    file_obj, filename = _pdf_file()

    response = await test_client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/paper-files",
        headers={"Idempotency-Key": "api-key-3"},
        files={"file": (filename, file_obj, "application/pdf")},
    )

    assert response.status_code == 404


async def test_upload_idempotent_conflict_returns_409(client) -> None:
    """相同 Idempotency-Key 不同文件应返回 409。"""
    test_client, project_id, *_ = client
    file_obj, filename = _pdf_file()

    await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        headers={"Idempotency-Key": "api-key-4"},
        files={"file": (filename, file_obj, "application/pdf")},
    )

    response = await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        headers={"Idempotency-Key": "api-key-4"},
        files={"file": ("other.pdf", BytesIO(b"%PDF-2\n"), "application/pdf")},
    )

    assert response.status_code == 409


async def test_upload_to_archived_project_returns_409(client) -> None:
    """向已归档 Project 上传返回 409 project_archived。"""
    test_client, project_id, project_repo, _ = client
    project = await project_repo.get_by_id(project_id)
    await project_repo.update(project.archive())
    file_obj, filename = _pdf_file()

    response = await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        headers={"Idempotency-Key": "archived-upload"},
        files={"file": (filename, file_obj, "application/pdf")},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "project_archived"


async def test_upload_same_hash_of_archived_paper_reuses_with_flag(client) -> None:
    """同 SHA-256 命中已归档 Paper：复用成功并返回 paper_archived=true。"""
    test_client, project_id, _, paper_repo = client
    file_obj, filename = _pdf_file()

    first = await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        headers={"Idempotency-Key": "archive-reuse-1"},
        files={"file": (filename, file_obj, "application/pdf")},
    )
    assert first.status_code == 202
    assert first.json()["paper_archived"] is False
    paper = await paper_repo.get_by_id(first.json()["paper_id"])
    await paper_repo.update(paper.archive())

    file_obj2, filename2 = _pdf_file()
    reused = await test_client.post(
        f"/api/v1/projects/{project_id}/paper-files",
        headers={"Idempotency-Key": "archive-reuse-2"},
        files={"file": (filename2, file_obj2, "application/pdf")},
    )

    # 同一 Project 已收录该 Paper，复用响应为 200（already_added）
    assert reused.status_code == 200
    assert reused.json()["reused"] is True
    assert reused.json()["already_added"] is True
    assert reused.json()["paper_archived"] is True
    assert reused.json()["paper_id"] == first.json()["paper_id"]
    # 不自动恢复归档
    still = await paper_repo.get_by_id(first.json()["paper_id"])
    assert still.is_archived is True
