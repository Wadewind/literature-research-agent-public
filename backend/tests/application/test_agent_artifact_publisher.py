"""正式 AgentArtifact 发布只依赖短事务内的 Candidate CAS。"""

from dataclasses import replace

import pytest

from literature_agent.application.agent_artifact_publisher import (
    RepositoryAgentArtifactPublisher,
)
from literature_agent.domain.research_agent import (
    AgentArtifactCandidateStatus,
    AgentSessionStatus,
    create_agent_artifact_candidate,
    create_agent_session,
    create_agent_turn_run,
)


class _Repository:
    def __init__(self):
        self.session = replace(
            create_agent_session(
                owner_id="owner-1", project_id="project-1", title=None
            ),
            session_id="session-1",
            active_turn_run_id="turn-1",
            status=AgentSessionStatus.ACTIVE,
        )
        self.turn = create_agent_turn_run(
            turn_run_id="turn-1",
            session_id="session-1",
            user_message_id="message-1",
            context_snapshot_id="context-1",
            policy_snapshot_id="policy-1",
        )
        self.candidate = create_agent_artifact_candidate(
            candidate_id="candidate-1",
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            name="chart.png",
            media_type="image/png",
            content_ref="/workspace/outputs/chart.png",
            content_hash="a" * 64,
            size_bytes=24,
        ).validate(
            tool_call_id="call-1",
            storage_key="agent-artifacts/owner-1/session-1/turn-1/staging/hash",
            sandbox_generation=1,
            sandbox_fencing_token=1,
        )
        self.artifacts = []

    async def get_turn_scoped(self, run_id, owner_id):
        return self.turn if (run_id, owner_id) == ("turn-1", "owner-1") else None

    async def get_session_scoped(self, session_id, owner_id):
        return self.session if (session_id, owner_id) == ("session-1", "owner-1") else None

    async def list_artifacts_scoped(self, run_id, owner_id):
        return list(self.artifacts)

    async def list_candidates_scoped(self, run_id, owner_id):
        return [self.candidate]

    async def save_candidate(self, value, *, expected_status):
        assert expected_status == AgentArtifactCandidateStatus.VALIDATED.value
        if self.candidate.status is not AgentArtifactCandidateStatus.VALIDATED:
            return False
        self.candidate = value
        return True

    async def get_candidate(self, candidate_id):
        return self.candidate if candidate_id == self.candidate.candidate_id else None

    async def is_sandbox_fence_current(self, **scope):
        return (
            scope["owner_id"],
            scope["project_id"],
            scope["session_id"],
            scope["turn_run_id"],
            scope["sandbox_generation"],
            scope["sandbox_fencing_token"],
        ) == ("owner-1", "project-1", "session-1", "turn-1", 1, 1)

    async def add_artifact_if_absent(self, value):
        if not self.artifacts:
            self.artifacts.append(value)
        return self.artifacts[0]


@pytest.mark.asyncio
async def test_publish_validated_candidate_is_stable_and_effectively_once() -> None:
    repository = _Repository()
    publisher = RepositoryAgentArtifactPublisher(repository)

    first = await publisher.publish_for_success(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
    )
    second = await publisher.publish_for_success(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
    )

    assert first[0].artifact_id == second[0].artifact_id
    assert repository.candidate.status is AgentArtifactCandidateStatus.COMMITTED
    assert len(repository.artifacts) == 1


@pytest.mark.asyncio
async def test_staged_or_rejected_candidate_is_never_published() -> None:
    repository = _Repository()
    repository.candidate = create_agent_artifact_candidate(
        candidate_id="candidate-2",
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        name="note.md",
        media_type="text/markdown",
        content_ref="descriptor://fake",
        content_hash="b" * 64,
        size_bytes=10,
    )

    result = await RepositoryAgentArtifactPublisher(repository).publish_for_success(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
    )

    assert result == ()
    assert repository.artifacts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_id", "project_id", "session_id"),
    [
        ("owner-2", "project-1", "session-1"),
        ("owner-1", "project-2", "session-1"),
        ("owner-1", "project-1", "session-2"),
    ],
)
async def test_publish_rejects_cross_owner_project_or_session_scope(
    owner_id: str, project_id: str, session_id: str
) -> None:
    repository = _Repository()

    with pytest.raises(ValueError, match="agent_artifact_publish_scope_invalid"):
        await RepositoryAgentArtifactPublisher(repository).publish_for_success(
            owner_id=owner_id,
            project_id=project_id,
            session_id=session_id,
            turn_run_id="turn-1",
        )

    assert repository.candidate.status is AgentArtifactCandidateStatus.VALIDATED
    assert repository.artifacts == []


@pytest.mark.asyncio
async def test_publish_rejects_validated_candidate_after_sandbox_fence_rotates() -> None:
    repository = _Repository()

    async def _stale(**scope):
        del scope
        return False

    repository.is_sandbox_fence_current = _stale
    with pytest.raises(ValueError, match="agent_artifact_sandbox_fence_lost"):
        await RepositoryAgentArtifactPublisher(repository).publish_for_success(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
        )

    assert repository.candidate.status is AgentArtifactCandidateStatus.VALIDATED
    assert repository.artifacts == []
