"""Agent 附件物化的取消、内容和顺序边界。"""

from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from literature_agent.application.agent_attachment_materializer import (
    AgentAttachmentMaterializationError,
    AgentAttachmentMaterializer,
)
from literature_agent.domain.agent_attachment import (
    agent_attachment_request_hash,
    create_agent_attachment,
    validate_agent_attachment_content,
)
from literature_agent.domain.research_agent import (
    AttachmentContextRef,
    create_agent_session,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    _runtime_user_message_content,
)
from tests.infrastructure.test_deep_agents_research_agent_runtime import _request


class _Db:
    async def commit(self): ...
    async def flush(self): ...
    async def rollback(self): ...


class _AgentRepo:
    def __init__(self, session):
        self.session = session

    async def get_session_scoped(self, session_id, owner_id):
        return self.session if (session_id, owner_id) == ("session-1", "owner-1") else None


class _RunRepo:
    def __init__(self, run):
        self.run = run

    async def get_by_id(self, run_id):
        return self.run if run_id == "turn-1" else None


class _AttachmentRepo:
    def __init__(self, value):
        self.value = value

    async def get_many_available_scoped(
        self, ids, session_id, owner_id, *, for_update=False
    ):
        assert not for_update
        return [self.value] if ids == (self.value.attachment_id,) else []


class _Storage:
    def __init__(self, content):
        self.content = content

    async def read(self, key):
        del key
        return self.content


class _Inbox:
    def __init__(self):
        self.calls = []

    async def assert_current(self):
        self.calls.append("current")

    async def reset(self):
        self.calls.append("reset")

    async def upload(self, path, content):
        self.calls.append((path, content))


def _fixture(*, storage_content: bytes = b"research notes"):
    validated = validate_agent_attachment_content(
        display_name="notes.txt", media_type="text/plain", content=b"research notes"
    )
    value = create_agent_attachment(
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
    request = _request(turn_run_id="turn-1")
    request = replace(
        request,
        context_snapshot=replace(
            request.context_snapshot,
            attachment_refs=(
                AttachmentContextRef(
                    value.attachment_id,
                    value.version,
                    value.content_hash,
                    value.size_bytes,
                    value.media_type,
                    value.display_name,
                ),
            ),
        ),
    )
    session = replace(
        create_agent_session(owner_id="owner-1", project_id="project-1", title=None),
        session_id="session-1",
        active_turn_run_id="turn-1",
    )
    run = replace(
        create_run("project-1", "owner-1", RunType.AGENT_TURN),
        run_id="turn-1",
        status=RunStatus.RUNNING,
    )
    db = _Db()

    @asynccontextmanager
    async def session_factory():
        yield db

    materializer = AgentAttachmentMaterializer(
        session_factory=session_factory,
        agent_repo_factory=lambda _: _AgentRepo(session),
        run_repo_factory=lambda _: _RunRepo(run),
        attachment_repo_factory=lambda _: _AttachmentRepo(value),
        storage=_Storage(storage_content),
    )
    return materializer, request


async def test_materializes_only_frozen_attachment_to_opaque_inbox_path() -> None:
    materializer, request = _fixture()
    inbox = _Inbox()

    await materializer.materialize(request, inbox)

    upload = next(value for value in inbox.calls if isinstance(value, tuple))
    assert upload[0].startswith("/workspace/inbox/")
    assert upload[0].endswith("/notes.txt")
    assert upload[1] == b"research notes"
    assert inbox.calls.index("reset") < inbox.calls.index(upload)


async def test_hash_drift_fails_before_upload() -> None:
    materializer, request = _fixture(storage_content=b"changed")
    inbox = _Inbox()

    with pytest.raises(AgentAttachmentMaterializationError):
        await materializer.materialize(request, inbox)

    assert not any(isinstance(value, tuple) for value in inbox.calls)


async def test_empty_turn_still_resets_previous_inbox() -> None:
    materializer, request = _fixture()
    request = replace(
        request,
        context_snapshot=replace(request.context_snapshot, attachment_refs=()),
    )
    inbox = _Inbox()

    await materializer.materialize(request, inbox)

    assert "reset" in inbox.calls
    assert not any(isinstance(value, tuple) for value in inbox.calls)


def test_deep_agent_message_sees_controlled_manifest_but_not_file_content() -> None:
    _, request = _fixture()

    content = _runtime_user_message_content(request)

    assert "/workspace/inbox/" in content
    assert "notes.txt" in content
    assert "research notes" not in content
    assert "storage_key" not in content
