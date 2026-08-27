"""Phase 5 Agent Session API 契约。"""

from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from literature_agent.api.agent_sessions import (
    get_agent_artifact_query_service,
    get_agent_session_service,
    get_mcp_configuration_service,
    get_skill_configuration_service,
    router,
)
from literature_agent.api.dependencies import get_actor
from literature_agent.application.agent_artifact_service import AgentArtifactContent
from literature_agent.application.agent_session_service import (
    AgentCitationView,
    AgentClaimView,
    AgentMessageView,
    PostAgentMessageResult,
)
from literature_agent.application.mcp_configuration_service import McpProfileView
from literature_agent.application.skill_configuration_service import SkillProfileView
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.agent_artifact import AgentArtifact
from literature_agent.domain.exceptions import AgentArtifactNotFoundError, AgentTurnNotFoundError
from literature_agent.domain.mcp_configuration import create_mcp_profile
from literature_agent.domain.research_agent import (
    AgentMessageRole,
    create_agent_message,
    create_agent_session,
)
from literature_agent.domain.skill_configuration import (
    create_owner_skill,
    create_skill_version,
)
from literature_agent.infrastructure.agent.skill_catalog import EVIDENCE_LED_SYNTHESIS
from literature_agent.main import create_app


def test_agent_session_router_is_available() -> None:
    """Agent Chat 使用独立公开资源，不暴露 Runtime SDK 配置。"""
    assert router.prefix == "/api/v1"


class _Service:
    post_calls = 0

    async def create_session(self, actor, project_id, *, title):
        return create_agent_session(owner_id=actor.owner_id, project_id=project_id, title=title)

    async def list_sessions(self, actor, project_id):
        return [
            create_agent_session(
                owner_id=actor.owner_id, project_id=project_id, title="最近研究"
            )
        ]

    async def get_project_ready_index_count(self, actor, project_id):
        assert actor.owner_id == "owner-1"
        assert project_id == "project-1"
        return 25

    async def list_message_views(self, actor, session_id):
        del actor
        user = create_agent_message(
            session_id=session_id,
            last_sequence=0,
            role=AgentMessageRole.USER,
            content="问题",
            turn_run_id="run-1",
            idempotency_key="key-1",
        )
        assistant = replace(
            create_agent_message(
                session_id=session_id,
                last_sequence=1,
                role=AgentMessageRole.ASSISTANT,
                content="结论",
                turn_run_id="run-1",
                idempotency_key="assistant:run-1",
            ),
            claim_set_id="claim-set-1",
        )
        return [
            AgentMessageView(user, None),
            AgentMessageView(
                assistant,
                (
                    AgentClaimView(
                        "结论",
                        (
                            AgentCitationView(
                                "evidence-1",
                                "paper-1",
                                "version-1",
                                "Results",
                                3,
                                3,
                                "有界证据摘录",
                            ),
                        ),
                    ),
                ),
            ),
        ]

    async def post_message(self, actor, session_id, **kwargs):
        type(self).post_calls += 1
        return PostAgentMessageResult("message-1", "run-1", "queued")


class _McpService:
    async def get_profile(self, actor, session_id):
        return McpProfileView(session_id, 0, (), "0" * 64)

    async def update_profile(self, actor, session_id, **kwargs):
        create_mcp_profile(
            owner_id=actor.owner_id,
            session_id=session_id,
            selections=kwargs["selections"],
        )
        return McpProfileView(
            session_id,
            1,
            kwargs["selections"],
            "1" * 64,
        )

    def list_catalog(self):
        return ()


class _SkillService:
    async def list_available(self, actor):
        del actor
        return (EVIDENCE_LED_SYNTHESIS,)

    async def create_owner_skill(self, actor, **kwargs):
        name = kwargs.pop("name")
        identity = create_owner_skill(owner_id=actor.owner_id, name=name)
        return create_skill_version(skill=identity, **kwargs)

    async def create_owner_version(self, actor, skill_id, **kwargs):
        del actor, skill_id, kwargs
        return EVIDENCE_LED_SYNTHESIS

    async def get_profile(self, actor, session_id):
        del actor
        return SkillProfileView(session_id, 0, (), "0" * 64)

    async def update_profile(self, actor, session_id, **kwargs):
        del actor
        return SkillProfileView(
            session_id,
            1,
            kwargs["selections"],
            "1" * 64,
        )


class _ArtifactService:
    artifact = AgentArtifact(
        artifact_id="00000000-0000-0000-0000-000000000001",
        candidate_id="candidate-1",
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="run-1",
        name="研究 图.png",
        media_type="image/png",
        content_hash="a" * 64,
        size_bytes=24,
        storage_key="private/key",
        created_at=datetime.now(UTC),
    )
    content_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    async def list_artifacts(self, owner_id, run_id):
        if (owner_id, run_id) != ("owner-1", "run-1"):
            raise AgentTurnNotFoundError(run_id)
        return (self.artifact,)

    async def content(self, owner_id, artifact_id):
        if (owner_id, artifact_id) != ("owner-1", self.artifact.artifact_id):
            raise AgentArtifactNotFoundError(artifact_id)
        return AgentArtifactContent(self.artifact, self.content_bytes)


def test_agent_artifact_api_hides_storage_and_serves_safe_verified_content() -> None:
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_agent_artifact_query_service] = _ArtifactService
    with TestClient(app) as client:
        listed = client.get("/api/v1/agent-turn-runs/run-1/artifacts")
        content = client.get(
            "/api/v1/agent-artifacts/00000000-0000-0000-0000-000000000001/content"
        )
        missing = client.get("/api/v1/agent-artifacts/missing/content")

    assert listed.status_code == 200
    assert listed.json()[0]["previewable"] is True
    assert "storage_key" not in listed.json()[0]
    assert content.content == _ArtifactService.content_bytes
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-disposition"].startswith("inline;")
    assert missing.status_code == 404


def test_agent_artifact_api_forces_pdf_to_attachment() -> None:
    class _PdfArtifactService(_ArtifactService):
        artifact = replace(
            _ArtifactService.artifact,
            name="report.pdf",
            media_type="application/pdf",
        )

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_agent_artifact_query_service] = _PdfArtifactService
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agent-artifacts/00000000-0000-0000-0000-000000000001/content"
        )
    assert response.headers["content-disposition"].startswith("attachment;")


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


def test_agent_api_lists_project_sessions_and_returns_persisted_claim_summaries() -> None:
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_agent_session_service] = _Service
    with TestClient(app) as client:
        sessions = client.get("/api/v1/projects/project-1/agent-sessions")
        context = client.get("/api/v1/projects/project-1/agent-context-summary")
        messages = client.get("/api/v1/agent-sessions/session-1/messages")

    assert sessions.status_code == 200
    assert sessions.json()[0]["title"] == "最近研究"
    assert context.status_code == 200
    assert context.json() == {"ready_index_count": 25}
    assert messages.status_code == 200
    assert messages.json()[0]["claims"] is None
    assert messages.json()[1]["claim_set_id"] == "claim-set-1"
    assert messages.json()[1]["claims"] == [
        {
            "text": "结论",
            "citations": [
                {
                    "evidence_id": "evidence-1",
                    "paper_id": "paper-1",
                    "version_id": "version-1",
                    "section_path": "Results",
                    "page_start": 3,
                    "page_end": 3,
                    "excerpt": "有界证据摘录",
                }
            ],
        }
    ]


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


def test_mcp_profile_api_accepts_only_catalog_selection_and_safe_parameters() -> None:
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_mcp_configuration_service] = _McpService
    with TestClient(app) as client:
        empty_catalog = client.get("/api/v1/agent-mcp-catalog")
        empty_profile = client.get("/api/v1/agent-sessions/session-1/mcp-profile")
        updated = client.put(
            "/api/v1/agent-sessions/session-1/mcp-profile",
            json={
                "expected_revision": 0,
                "selections": [
                    {
                        "catalog_id": "fixture-search",
                        "version": "1.0.0",
                        "parameters": {"corpus": "papers"},
                    }
                ],
            },
        )
        rejected_connection = client.put(
            "/api/v1/agent-sessions/session-1/mcp-profile",
            json={
                "expected_revision": 1,
                "selections": [],
                "endpoint": "http://internal",
            },
        )
        rejected_secret = client.put(
            "/api/v1/agent-sessions/session-1/mcp-profile",
            json={
                "expected_revision": 1,
                "selections": [
                    {
                        "catalog_id": "fixture-search",
                        "version": "1.0.0",
                        "parameters": {"token": "do-not-store"},
                    }
                ],
            },
        )
        rejected_duplicate_catalog = client.put(
            "/api/v1/agent-sessions/session-1/mcp-profile",
            json={
                "expected_revision": 1,
                "selections": [
                    {"catalog_id": "fixture-search", "version": "1.0.0"},
                    {"catalog_id": "fixture-search", "version": "2.0.0"},
                ],
            },
        )

    assert empty_catalog.status_code == 200 and empty_catalog.json() == []
    assert empty_profile.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["selections"][0]["parameters"] == {"corpus": "papers"}
    assert rejected_connection.status_code == 422
    assert rejected_secret.status_code == 422
    assert rejected_duplicate_catalog.status_code == 422
    payload = str(updated.json()) + str(empty_profile.json())
    assert "endpoint" not in payload and "transport" not in payload and "env" not in payload


def test_skill_api_accepts_only_declarative_content_and_catalog_selection() -> None:
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="owner-1")
    app.dependency_overrides[get_skill_configuration_service] = _SkillService
    with TestClient(app) as client:
        catalog = client.get("/api/v1/agent-skills")
        created = client.post(
            "/api/v1/agent-skills",
            json={
                "name": "compare-studies",
                "description": "比较研究",
                "instructions": "先读取 Evidence Matrix。",
                "required_tool_names": ["read_review_evidence_matrix"],
            },
        )
        profile = client.put(
            "/api/v1/agent-sessions/session-1/skill-profile",
            json={
                "expected_revision": 0,
                "selections": [
                    {
                        "source": "platform",
                        "skill_id": EVIDENCE_LED_SYNTHESIS.skill_id,
                        "version": 1,
                    }
                ],
            },
        )
        rejected_path = client.post(
            "/api/v1/agent-skills",
            json={
                "name": "unsafe",
                "description": "unsafe",
                "instructions": "unsafe",
                "required_tool_names": [],
                "path": "/host/skills",
                "frontmatter": "---",
                "scripts": ["run.py"],
                "content_hash": "f" * 64,
            },
        )
        rejected_owner = client.post(
            "/api/v1/agent-skills",
            json={
                "name": "unsafe-owner",
                "description": "unsafe",
                "instructions": "unsafe",
                "required_tool_names": [],
                "owner_id": "other",
            },
        )

    assert catalog.status_code == 200
    assert catalog.json()[0]["instructions"]
    assert created.status_code == 201
    assert created.json()["source"] == "owner"
    assert created.json()["instructions"] == "先读取 Evidence Matrix。"
    assert profile.status_code == 200
    assert profile.json()["selections"] == [
        {
            "source": "platform",
            "skill_id": EVIDENCE_LED_SYNTHESIS.skill_id,
            "version": 1,
        }
    ]
    assert rejected_path.status_code == 422
    assert rejected_owner.status_code == 422
