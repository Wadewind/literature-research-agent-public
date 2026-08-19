"""最小健康检查端点与应用工厂的测试。"""

import pytest
from fastapi.testclient import TestClient

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


@pytest.mark.asyncio
async def test_lifespan_yields_app_state() -> None:
    """应用 lifespan 应创建并产出 AppState 对象。"""
    app = create_app()

    async with app.router.lifespan_context(app) as state:
        assert isinstance(state, dict)
        app_state = state["app_state"]
        assert isinstance(app_state, AppState)
        assert isinstance(app_state.settings, Settings)
