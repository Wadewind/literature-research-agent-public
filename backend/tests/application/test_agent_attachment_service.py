"""AgentAttachment 上传幂等、事务外 I/O 与引用删除。"""

from contextlib import asynccontextmanager

import pytest

from literature_agent.application.agent_attachment_service import AgentAttachmentService
from literature_agent.application.ports.agent_attachment_repository import (
    AgentAttachmentInsertResult,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    AgentAttachmentReferencedError,
    IdempotencyConflictError,
)
from literature_agent.domain.research_agent import create_agent_session


class _Db:
    def __init__(self, state):
        self.state = state

    async def commit(self):
        self.state["commits"] += 1

    async def flush(self): ...
    async def rollback(self): ...


class _AgentRepo:
    def __init__(self, session):
        self.session = session

    async def get_session_scoped(self, session_id, owner_id):
        return self.session if (session_id, owner_id) == ("session-1", "owner-1") else None


class _Attachments:
    def __init__(self):
        self.values = {}
        self.referenced = False

    async def get_by_idempotency_key(self, owner_id, key):
        return self.values.get((owner_id, key))

    async def add_if_absent(self, value):
        self.values[(value.owner_id, value.idempotency_key)] = value
        return AgentAttachmentInsertResult(value, True)

    async def list_scoped(self, session_id, owner_id):
        return [
            value
            for value in self.values.values()
            if (value.session_id, value.owner_id) == (session_id, owner_id)
        ]

    async def get_scoped(
        self, attachment_id, session_id, owner_id, *, for_update=False
    ):
        assert for_update
        return next(
            (
                value
                for value in self.values.values()
                if (value.attachment_id, value.session_id, value.owner_id)
                == (attachment_id, session_id, owner_id)
            ),
            None,
        )

    async def is_referenced(self, attachment_id):
        del attachment_id
        return self.referenced

    async def mark_deleted(self, value):
        self.values[(value.owner_id, value.idempotency_key)] = value
        return True


class _Storage:
    def __init__(self, state):
        self.state = state
        self.values = {}

    async def write(self, key, content):
        assert self.state["open"] == 0
        self.values[key] = content


def _service():
    state = {"open": 0, "commits": 0}
    db = _Db(state)
    session = create_agent_session(owner_id="owner-1", project_id="project-1", title=None)
    session = type(session)(
        "session-1", session.owner_id, session.project_id, session.title,
        session.status, session.active_turn_run_id, session.created_at, session.last_activity_at,
    )
    attachments = _Attachments()
    storage = _Storage(state)

    @asynccontextmanager
    async def factory():
        state["open"] += 1
        try:
            yield db
        finally:
            state["open"] -= 1

    service = AgentAttachmentService(
        session_factory=factory,
        agent_repo_factory=lambda _: _AgentRepo(session),
        attachment_repo_factory=lambda _: attachments,
        storage=storage,
    )
    return service, attachments, storage


async def test_upload_writes_outside_transaction_and_replays_same_request() -> None:
    service, attachments, storage = _service()
    actor = ActorContext("owner-1")

    first = await service.upload(
        actor, "session-1", display_name="notes.txt", media_type="text/plain",
        content=b"notes", idempotency_key="upload-1",
    )
    replay = await service.upload(
        actor, "session-1", display_name="notes.txt", media_type="text/plain",
        content=b"notes", idempotency_key="upload-1",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.attachment.attachment_id == first.attachment.attachment_id
    assert len(storage.values) == 1
    assert len(attachments.values) == 1


async def test_idempotency_key_rejects_changed_content_and_reference_blocks_delete() -> None:
    service, attachments, _ = _service()
    actor = ActorContext("owner-1")
    first = await service.upload(
        actor, "session-1", display_name="notes.txt", media_type="text/plain",
        content=b"notes", idempotency_key="upload-1",
    )

    with pytest.raises(IdempotencyConflictError):
        await service.upload(
            actor, "session-1", display_name="notes.txt", media_type="text/plain",
            content=b"different", idempotency_key="upload-1",
        )
    attachments.referenced = True
    with pytest.raises(AgentAttachmentReferencedError):
        await service.delete(actor, "session-1", first.attachment.attachment_id)
