"""Run API 路由测试。"""

import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.runs import get_run_service
from literature_agent.application.run_service import RunService
from literature_agent.domain.actor import ActorContext
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


def _build_fake_service(
    fake_run_repo: FakeRunRepository,
    fake_event_repo: FakeEventRepository,
) -> RunService:
    """基于 Fake Repository 构建 RunService。"""
    return RunService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: fake_run_repo,
        event_repo_factory=lambda _session: fake_event_repo,
    )


@pytest_asyncio.fixture
async def client():
    """提供已注入 Fake 依赖的 TestClient。"""
    from literature_agent.main import create_app

    fake_run_repo = FakeRunRepository()
    fake_event_repo = FakeEventRepository()
    service = _build_fake_service(fake_run_repo, fake_event_repo)

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-1")
    app.dependency_overrides[get_run_service] = lambda: service

    with TestClient(app) as test_client:
        yield test_client, fake_run_repo, fake_event_repo

    app.dependency_overrides.clear()


def test_create_run(client) -> None:
    """POST /api/v1/runs 应创建 Run 并返回 201。"""
    test_client, _, _ = client

    response = test_client.post(
        "/api/v1/runs",
        json={
            "project_id": "project-1",
            "run_type": "ingestion",
            "input_payload": {"file": "x.pdf"},
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["run_type"] == "ingestion"
    assert data["status"] == "queued"
    assert data["owner_id"] == "user-1"


def test_get_run(client) -> None:
    """GET /api/v1/runs/{id} 返回 Run 详情。"""
    test_client, fake_run_repo, _ = client
    from literature_agent.domain.run import create_run

    run = create_run(project_id="p1", owner_id="user-1", run_type="ingestion")
    fake_run_repo._runs[run.run_id] = run

    response = test_client.get(f"/api/v1/runs/{run.run_id}")

    assert response.status_code == 200
    assert response.json()["run_id"] == run.run_id


def test_get_run_not_found(client) -> None:
    """GET /api/v1/runs/{id} 对不存在资源返回 404。"""
    test_client, _, _ = client

    response = test_client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_cancel_run(client) -> None:
    """POST /api/v1/runs/{id}/cancel 返回 202。"""
    test_client, fake_run_repo, _ = client
    from literature_agent.domain.run import create_run

    run = create_run(project_id="p1", owner_id="user-1", run_type="ingestion")
    fake_run_repo._runs[run.run_id] = run

    response = test_client.post(f"/api/v1/runs/{run.run_id}/cancel")

    assert response.status_code == 202


def test_list_events(client) -> None:
    """GET /api/v1/runs/{id}/events 返回事件列表。"""
    test_client, fake_run_repo, fake_event_repo = client
    from literature_agent.domain.event import create_event
    from literature_agent.domain.run import create_run

    run = create_run(project_id="p1", owner_id="user-1", run_type="ingestion")
    fake_run_repo._runs[run.run_id] = run
    event = create_event(run.run_id, 1, "run_created", "user", "corr-1")
    fake_event_repo._events.append(event)

    response = test_client.get(f"/api/v1/runs/{run.run_id}/events")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["event_type"] == "run_created"
