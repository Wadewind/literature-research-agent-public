"""最小健康检查端点与应用工厂的测试。"""

import pytest
from fastapi.testclient import TestClient

from literature_agent.api.health import get_readiness_service
from literature_agent.application.health_service import HealthService
from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.lifespan import AppState
from literature_agent.main import create_app


def test_health_live_returns_ok() -> None:
    """GET /health/live 应返回 200 OK 的存活响应。"""
    app = create_app()
    client = TestClient(app)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class _Probe:
    """API 健康检查使用的可控依赖探针。"""

    def __init__(self, name: str, *, available: bool = True) -> None:
        self.name = name
        self.available = available

    async def check(self) -> None:
        if not self.available:
            raise ConnectionError(f"{self.name} unavailable")


def test_health_ready_returns_dependency_status() -> None:
    """所有依赖可用时 ready 返回 200 和逐项状态。"""
    app = create_app()
    app.dependency_overrides[get_readiness_service] = lambda: HealthService(
        probes=(_Probe("postgres"), _Probe("valkey"))
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {"postgres": "ok", "valkey": "ok"},
    }


def test_health_ready_returns_503_when_dependency_is_unavailable() -> None:
    """任一依赖不可用时 ready 返回 503，但不泄露底层异常。"""
    app = create_app()
    app.dependency_overrides[get_readiness_service] = lambda: HealthService(
        probes=(_Probe("postgres"), _Probe("valkey", available=False))
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"postgres": "ok", "valkey": "unavailable"},
    }


@pytest.mark.asyncio
async def test_lifespan_yields_app_state() -> None:
    """应用 lifespan 应创建并产出 AppState 对象。"""
    app = create_app()

    async with app.router.lifespan_context(app) as state:
        assert isinstance(state, dict)
        app_state = state["app_state"]
        assert isinstance(app_state, AppState)
        assert isinstance(app_state.settings, Settings)
