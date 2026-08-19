"""Project API 路由测试。"""

import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.projects import get_project_service
from literature_agent.application.project_service import ProjectService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.project import create_project
from literature_agent.main import create_app
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session


def _build_fake_service(fake_repo: FakeProjectRepository) -> ProjectService:
    """基于 Fake Repository 构建 ProjectService。"""
    return ProjectService(
        session_factory=fake_session,
        repo_factory=lambda _session: fake_repo,
    )


@pytest_asyncio.fixture
async def client():
    """提供已注入 Fake 依赖的 TestClient。"""
    fake_repo = FakeProjectRepository()
    service = _build_fake_service(fake_repo)

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-1")
    app.dependency_overrides[get_project_service] = lambda: service

    with TestClient(app) as test_client:
        yield test_client, fake_repo

    app.dependency_overrides.clear()


def test_create_project(client) -> None:
    """POST /api/v1/projects 应创建 Project 并返回 201。"""
    test_client, _ = client

    response = test_client.post(
        "/api/v1/projects",
        json={"name": "API 测试项目", "description": "说明"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "API 测试项目"
    assert data["description"] == "说明"
    assert data["owner_id"] == "user-1"
    assert "project_id" in data


def test_list_projects_returns_only_owned(client) -> None:
    """GET /api/v1/projects 只返回当前 actor 的 Project。"""
    test_client, fake_repo = client
    project_a = create_project(owner_id="user-1", name="A", description="")
    project_b = create_project(owner_id="user-2", name="B", description="")
    fake_repo._projects[project_a.project_id] = project_a
    fake_repo._projects[project_b.project_id] = project_b

    response = test_client.get("/api/v1/projects")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "A"


def test_get_project_returns_owned(client) -> None:
    """GET /api/v1/projects/{id} 返回属于自己的 Project。"""
    test_client, fake_repo = client
    project = create_project(owner_id="user-1", name="我的项目", description="")
    fake_repo._projects[project.project_id] = project

    response = test_client.get(f"/api/v1/projects/{project.project_id}")

    assert response.status_code == 200
    assert response.json()["project_id"] == project.project_id


def test_get_project_owned_by_other_returns_404(client) -> None:
    """访问其他用户的 Project 应返回 404。"""
    test_client, fake_repo = client
    project = create_project(owner_id="user-2", name="B", description="")
    fake_repo._projects[project.project_id] = project

    response = test_client.get(f"/api/v1/projects/{project.project_id}")

    assert response.status_code == 404


def test_get_project_not_found_returns_404(client) -> None:
    """访问不存在的 Project 应返回 404。"""
    test_client, _ = client

    response = test_client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_create_project_rejects_empty_name(client) -> None:
    """创建时项目名称不能为空。"""
    test_client, _ = client

    response = test_client.post(
        "/api/v1/projects",
        json={"name": ""},
    )

    assert response.status_code == 422
