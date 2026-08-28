"""Agent Artifact Manifest 只投影正式、有界来源元数据。"""

from datetime import UTC, datetime
from types import SimpleNamespace

from literature_agent.api.agent_sessions import get_agent_artifact_manifest
from literature_agent.domain.agent_artifact import AgentArtifact


async def test_manifest_exposes_checked_declared_target_without_claiming_provenance() -> None:
    artifact = AgentArtifact(
        artifact_id="artifact-1",
        candidate_id="candidate-1",
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        name="paper.pdf",
        media_type="application/pdf",
        content_hash="a" * 64,
        size_bytes=128,
        storage_key="private/staging/key",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
        source_url="https://arxiv.org/pdf/2401.00001",
        source_url_hash="b" * 64,
    )
    local_artifact = AgentArtifact(
        artifact_id="artifact-2",
        candidate_id="candidate-2",
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        name="chart.png",
        media_type="image/png",
        content_hash="c" * 64,
        size_bytes=32,
        storage_key="private/staging/local-key",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    class _Service:
        async def list_artifacts(self, owner_id, run_id):
            assert (owner_id, run_id) == ("owner-1", "turn-1")
            return (artifact, local_artifact)

    response = await get_agent_artifact_manifest(
        "turn-1", SimpleNamespace(owner_id="owner-1"), _Service()
    )
    payload = response.model_dump(mode="json")

    assert payload["items"][0]["source_status"] == "declared_public_target_checked"
    assert payload["items"][0]["source_url"] == artifact.source_url
    assert payload["items"][0]["created_at"] == "2026-08-28T00:00:00Z"
    assert payload["items"][1]["source_status"] == "not_provided"
    assert "storage_key" not in payload["items"][0]
    assert "content" not in payload["items"][0]
