"""正式 AgentArtifact 查询只返回 owner-scoped 事实并复核 blob。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from literature_agent.application.agent_artifact_service import (
    AgentArtifactQueryService,
    AgentArtifactServiceError,
)
from literature_agent.domain.agent_artifact import AgentArtifact
from literature_agent.domain.exceptions import AgentArtifactNotFoundError, AgentTurnNotFoundError


def _artifact() -> AgentArtifact:
    return AgentArtifact(
        artifact_id="00000000-0000-0000-0000-000000000001",
        candidate_id="candidate-1",
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        name="note.md",
        media_type="text/markdown",
        content_hash="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        size_bytes=5,
        storage_key="private/key",
        created_at=datetime.now(UTC),
    )


class _Repository:
    async def get_turn_scoped(self, run_id, owner_id):
        return object() if (run_id, owner_id) == ("turn-1", "owner-1") else None

    async def list_artifacts_scoped(self, run_id, owner_id):
        return [_artifact()]

    async def get_artifact_scoped(self, artifact_id, owner_id):
        artifact = _artifact()
        if (artifact_id, owner_id) == (artifact.artifact_id, artifact.owner_id):
            return artifact
        return None


class _Storage:
    def __init__(self, content=b"hello") -> None:
        self.content = content

    async def read(self, key):
        assert key == "private/key"
        return self.content


@asynccontextmanager
async def _session_factory():
    yield object()


def _service(content=b"hello"):
    return AgentArtifactQueryService(
        session_factory=_session_factory,
        agent_repo_factory=lambda _: _Repository(),
        storage=_Storage(content),
    )


@pytest.mark.asyncio
async def test_query_hides_cross_owner_and_validates_content_integrity() -> None:
    service = _service()
    values = await service.list_artifacts("owner-1", "turn-1")
    result = await service.content("owner-1", values[0].artifact_id)
    assert result.content == b"hello"

    with pytest.raises(AgentTurnNotFoundError):
        await service.list_artifacts("owner-2", "turn-1")
    with pytest.raises(AgentArtifactNotFoundError):
        await service.content("owner-2", values[0].artifact_id)


@pytest.mark.asyncio
async def test_query_rejects_storage_hash_or_size_drift() -> None:
    with pytest.raises(AgentArtifactServiceError) as caught:
        await _service(b"tampered").content("owner-1", "00000000-0000-0000-0000-000000000001")
    assert caught.value.code == "artifact_content_integrity_failed"
