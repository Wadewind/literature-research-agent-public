"""Project API 路由测试。"""

import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.projects import get_project_service
from literature_agent.application.project_service import ProjectService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.project import create_project
from literature_agent.domain.run import create_run
from literature_agent.main import create_app
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


def _build_fake_service(
    fake_repo: FakeProjectRepository,
    fake_run_repo: FakeRunRepository,
) -> ProjectService:
    """基于 Fake Repository 构建 ProjectService。"""
    return ProjectService(
        session_factory=fake_session,
        repo_factory=lambda _session: fake_repo,
        run_repo_factory=lambda _session: fake_run_repo,
    )


@pytest_asyncio.fixture
async def client():
    """提供已注入 Fake 依赖的 TestClient。"""
    fake_repo = FakeProjectRepository()
    fake_run_repo = FakeRunRepository()
    service = _build_fake_service(fake_repo, fake_run_repo)

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-1")
    app.dependency_overrides[get_project_service] = lambda: service

    with TestClient(app) as test_client:
        yield test_client, fake_repo, fake_run_repo

    app.dependency_overrides.clear()


def test_create_project(client) -> None:
    """POST /api/v1/projects 应创建 Project 并返回 201。"""
    test_client, *_ = client

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
    test_client, fake_repo, _ = client
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
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-1", name="我的项目", description="")
    fake_repo._projects[project.project_id] = project

    response = test_client.get(f"/api/v1/projects/{project.project_id}")

    assert response.status_code == 200
    assert response.json()["project_id"] == project.project_id


def test_get_project_owned_by_other_returns_404(client) -> None:
    """访问其他用户的 Project 应返回 404。"""
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-2", name="B", description="")
    fake_repo._projects[project.project_id] = project

    response = test_client.get(f"/api/v1/projects/{project.project_id}")

    assert response.status_code == 404


def test_get_project_not_found_returns_404(client) -> None:
    """访问不存在的 Project 应返回 404。"""
    test_client, *_ = client

    response = test_client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_create_project_rejects_empty_name(client) -> None:
    """创建时项目名称不能为空。"""
    test_client, *_ = client

    response = test_client.post(
        "/api/v1/projects",
        json={"name": ""},
    )

    assert response.status_code == 422


def test_update_project(client) -> None:
    """PATCH /api/v1/projects/{id} 修改名称和说明，返回 200。"""
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-1", name="旧名称", description="旧说明")
    fake_repo._projects[project.project_id] = project

    response = test_client.patch(
        f"/api/v1/projects/{project.project_id}",
        json={"name": "新名称"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "新名称"
    assert data["description"] == "旧说明"
    assert data["archived_at"] is None


def test_update_project_requires_at_least_one_field(client) -> None:
    """PATCH 空请求体应返回 422。"""
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-1", name="项目", description="")
    fake_repo._projects[project.project_id] = project

    response = test_client.patch(f"/api/v1/projects/{project.project_id}", json={})

    assert response.status_code == 422


def test_update_project_rejects_invalid_name(client) -> None:
    """PATCH 名称沿用创建校验规则。"""
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-1", name="项目", description="")
    fake_repo._projects[project.project_id] = project

    empty = test_client.patch(f"/api/v1/projects/{project.project_id}", json={"name": ""})
    too_long = test_client.patch(
        f"/api/v1/projects/{project.project_id}", json={"name": "x" * 201}
    )

    assert empty.status_code == 422
    assert too_long.status_code == 422


def test_update_project_not_found_returns_404(client) -> None:
    """PATCH 不存在或越权的 Project 应返回 404。"""
    test_client, fake_repo, _ = client
    other = create_project(owner_id="user-2", name="B", description="")
    fake_repo._projects[other.project_id] = other

    missing = test_client.patch("/api/v1/projects/missing", json={"name": "x"})
    forbidden = test_client.patch(
        f"/api/v1/projects/{other.project_id}", json={"name": "x"}
    )

    assert missing.status_code == 404
    assert forbidden.status_code == 404


def test_update_archived_project_returns_409(client) -> None:
    """已归档 Project 拒绝修改，返回 409 project_archived。"""
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-1", name="项目", description="")
    fake_repo._projects[project.project_id] = project
    test_client.post(f"/api/v1/projects/{project.project_id}/archive")

    response = test_client.patch(
        f"/api/v1/projects/{project.project_id}",
        json={"name": "新名称"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "project_archived"


def test_archive_and_restore_project(client) -> None:
    """归档与恢复均返回 200 且幂等。"""
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-1", name="项目", description="")
    fake_repo._projects[project.project_id] = project

    archived = test_client.post(f"/api/v1/projects/{project.project_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    again = test_client.post(f"/api/v1/projects/{project.project_id}/archive")
    assert again.status_code == 200
    assert again.json()["archived_at"] == archived.json()["archived_at"]

    restored = test_client.post(f"/api/v1/projects/{project.project_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None

    restore_again = test_client.post(f"/api/v1/projects/{project.project_id}/restore")
    assert restore_again.status_code == 200
    assert restore_again.json()["archived_at"] is None


def test_archive_project_with_active_run_returns_409(client) -> None:
    """存在非终态 Run 时归档返回 409 project_has_active_runs。"""
    test_client, fake_repo, fake_run_repo = client
    project = create_project(owner_id="user-1", name="项目", description="")
    fake_repo._projects[project.project_id] = project
    run = create_run(project.project_id, "user-1", "ingestion")
    fake_run_repo._runs[run.run_id] = run

    response = test_client.post(f"/api/v1/projects/{project.project_id}/archive")

    assert response.status_code == 409
    assert response.json()["detail"] == "project_has_active_runs"


def test_archive_project_not_found_returns_404(client) -> None:
    """归档/恢复不存在或越权的 Project 应返回 404。"""
    test_client, fake_repo, _ = client
    other = create_project(owner_id="user-2", name="B", description="")
    fake_repo._projects[other.project_id] = other

    assert test_client.post("/api/v1/projects/missing/archive").status_code == 404
    assert test_client.post("/api/v1/projects/missing/restore").status_code == 404
    assert (
        test_client.post(f"/api/v1/projects/{other.project_id}/archive").status_code == 404
    )


def test_list_projects_excludes_archived_by_default(client) -> None:
    """默认列表只返回 active Project，include_archived=true 返回全部。"""
    test_client, fake_repo, _ = client
    active = create_project(owner_id="user-1", name="活动", description="")
    archived = create_project(owner_id="user-1", name="归档", description="").archive()
    fake_repo._projects[active.project_id] = active
    fake_repo._projects[archived.project_id] = archived

    default = test_client.get("/api/v1/projects")
    full = test_client.get("/api/v1/projects", params={"include_archived": "true"})

    assert default.status_code == 200
    assert [p["name"] for p in default.json()] == ["活动"]
    assert full.status_code == 200
    assert len(full.json()) == 2


def test_get_archived_project_still_readable(client) -> None:
    """已归档 Project 的读接口保持可用。"""
    test_client, fake_repo, _ = client
    project = create_project(owner_id="user-1", name="项目", description="").archive()
    fake_repo._projects[project.project_id] = project

    response = test_client.get(f"/api/v1/projects/{project.project_id}")

    assert response.status_code == 200
    assert response.json()["archived_at"] is not None
