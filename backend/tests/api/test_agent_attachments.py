"""AgentAttachment 公开 API 的作用域、幂等和 DTO 边界。"""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from literature_agent.api.agent_attachments import get_service, router
from literature_agent.api.dependencies import get_actor
from literature_agent.application.agent_attachment_service import AgentAttachmentUploadResult
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.agent_attachment import (
    agent_attachment_request_hash,
    create_agent_attachment,
    validate_agent_attachment_content,
)
from literature_agent.domain.exceptions import AgentAttachmentReferencedError


def _attachment():
    validated = validate_agent_attachment_content(
        display_name="notes.txt", media_type="text/plain", content=b"notes"
    )
    return create_agent_attachment(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        display_name=validated.display_name,
        media_type=validated.media_type,
        content_hash=validated.content_hash,
        size_bytes=validated.size_bytes,
        idempotency_key="upload-1",
        request_hash=agent_attachment_request_hash(
            session_id="session-1",
            display_name=validated.display_name,
            media_type=validated.media_type,
            content_hash=validated.content_hash,
        ),
    )


class _Service:
    def __init__(self):
        self.value = _attachment()
        self.replayed = False
        self.referenced = False

    async def upload(self, actor, session_id, **kwargs):
        assert actor.owner_id == "owner-1"
        assert session_id == "session-1"
        assert "owner_id" not in kwargs
        return AgentAttachmentUploadResult(self.value, self.replayed)

    async def list(self, actor, session_id):
        assert actor.owner_id == "owner-1"
        assert session_id == "session-1"
        return [self.value]

    async def delete(self, actor, session_id, attachment_id):
        assert actor.owner_id == "owner-1"
        assert (session_id, attachment_id) == ("session-1", self.value.attachment_id)
        if self.referenced:
            raise AgentAttachmentReferencedError(attachment_id)


async def test_upload_list_delete_hide_internal_paths_and_preserve_replay_status() -> None:
    service = _Service()
    app = FastAPI()
    app.include_router(router)
    async def actor():
        return ActorContext("owner-1")

    async def attachment_service():
        return service

    app.dependency_overrides[get_actor] = actor
    app.dependency_overrides[get_service] = attachment_service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post(
            "/api/v1/agent-sessions/session-1/attachments",
            headers={"Idempotency-Key": "upload-1"},
            files={"file": ("notes.txt", b"notes", "text/plain")},
        )
        listed = await client.get("/api/v1/agent-sessions/session-1/attachments")
        deleted = await client.delete(
            f"/api/v1/agent-sessions/session-1/attachments/{service.value.attachment_id}"
        )
        service.replayed = True
        replayed = await client.post(
            "/api/v1/agent-sessions/session-1/attachments",
            headers={"Idempotency-Key": "upload-1"},
            files={"file": ("notes.txt", b"notes", "text/plain")},
        )

    assert uploaded.status_code == 201
    assert replayed.status_code == 200
    assert listed.status_code == 200
    assert deleted.status_code == 204
    assert "storage_key" not in uploaded.json()
    assert "path" not in uploaded.json()


async def test_upload_requires_idempotency_and_referenced_delete_conflicts() -> None:
    service = _Service()
    service.referenced = True
    app = FastAPI()
    app.include_router(router)
    async def actor():
        return ActorContext("owner-1")

    async def attachment_service():
        return service

    app.dependency_overrides[get_actor] = actor
    app.dependency_overrides[get_service] = attachment_service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing_key = await client.post(
            "/api/v1/agent-sessions/session-1/attachments",
            files={"file": ("notes.txt", b"notes", "text/plain")},
        )
        conflict = await client.delete(
            f"/api/v1/agent-sessions/session-1/attachments/{service.value.attachment_id}"
        )

    assert missing_key.status_code == 400
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "attachment_referenced"
