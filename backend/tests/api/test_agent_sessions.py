"""Phase 5 切片 2：Agent Session API 契约。"""

from fastapi.testclient import TestClient

from literature_agent.api.agent_sessions import get_agent_session_service, router
from literature_agent.api.dependencies import get_actor
from literature_agent.application.agent_session_service import PostAgentMessageResult
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.research_agent import create_agent_session
from literature_agent.main import create_app


def test_agent_session_router_is_available() -> None:
    """Agent Chat 使用独立公开资源，不暴露 Runtime SDK 配置。"""
    assert router.prefix == "/api/v1"


class _Service:
    post_calls = 0

    async def create_session(self, actor, project_id, *, title):
        return create_agent_session(owner_id=actor.owner_id, project_id=project_id, title=title)

    async def post_message(self, actor, session_id, **kwargs):
        type(self).post_calls += 1
        return PostAgentMessageResult("message-1", "run-1", "queued")


def test_agent_api_returns_201_and_202_and_rejects_runtime_configuration() -> None:
    _Service.post_calls = 0
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_agent_session_service] = _Service
    with TestClient(app) as client:
        created = client.post("/api/v1/projects/project-1/agent-sessions", json={"title": "研究"})
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        response = client.post(
            f"/api/v1/agent-sessions/{session_id}/messages",
            json={"content": "分析", "review_output_id": "output-1"},
            headers={"Idempotency-Key": "key-1"},
        )
        assert response.status_code == 202
        assert response.json() == {
            "user_message_id": "message-1",
            "run_id": "run-1",
            "status": "queued",
        }
        rejected = client.post(
            f"/api/v1/agent-sessions/{session_id}/messages",
            json={"content": "分析", "review_output_id": "output-1", "network_enabled": True},
            headers={"Idempotency-Key": "key-2"},
        )
        assert rejected.status_code == 422


def test_agent_message_requires_bounded_idempotency_key_as_http_400() -> None:
    _Service.post_calls = 0
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_agent_session_service] = _Service
    body = {"content": "分析", "review_output_id": "output-1"}
    with TestClient(app) as client:
        missing = client.post("/api/v1/agent-sessions/session-1/messages", json=body)
        too_long = client.post(
            "/api/v1/agent-sessions/session-1/messages",
            json=body,
            headers={"Idempotency-Key": "k" * 256},
        )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "idempotency_key_required"
    assert too_long.status_code == 400
    assert too_long.json()["detail"] == "idempotency_key_invalid"
    assert _Service.post_calls == 0


def test_agent_message_rejects_blank_content_at_http_boundary() -> None:
    _Service.post_calls = 0
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_agent_session_service] = _Service
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent-sessions/session-1/messages",
            json={"content": "   ", "review_output_id": "output-1"},
            headers={"Idempotency-Key": "key-blank-content"},
        )
    assert response.status_code == 422
    assert _Service.post_calls == 0
