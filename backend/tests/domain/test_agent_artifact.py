"""Agent Artifact 的路径、内容与不可变正式产物契约。"""

from dataclasses import FrozenInstanceError

import pytest

from literature_agent.domain.agent_artifact import (
    AGENT_ARTIFACT_MAX_BYTES,
    AgentArtifactValidationError,
    agent_artifact_candidate_id,
    agent_artifact_storage_key,
    create_agent_artifact,
    is_agent_artifact_output_path,
    validate_agent_artifact_content,
)
from literature_agent.domain.research_agent import create_agent_artifact_candidate


@pytest.mark.parametrize(
    "path",
    [
        "/workspace/outputs",
        "/workspace/outputs/",
        "/workspace/outputs/../secret",
        "/workspace/outputs//chart.png",
        "/workspace/chart.png",
        "outputs/chart.png",
    ],
)
def test_artifact_output_path_rejects_ambiguous_or_out_of_scope_paths(path: str) -> None:
    assert is_agent_artifact_output_path(path) is False


def test_artifact_content_validates_magic_text_json_and_svg_active_content() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    value = validate_agent_artifact_content(name="chart.png", media_type="image/png", content=png)
    assert value.size_bytes == len(png)

    validate_agent_artifact_content(
        name="data.json", media_type="application/json", content=b'{"ok":true}'
    )
    with pytest.raises(AgentArtifactValidationError) as mismatch:
        validate_agent_artifact_content(
            name="chart.png", media_type="image/png", content=b"not-png"
        )
    assert mismatch.value.code == "artifact_magic_mismatch"
    with pytest.raises(AgentArtifactValidationError) as active:
        validate_agent_artifact_content(
            name="chart.svg",
            media_type="image/svg+xml",
            content=b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
        )
    assert active.value.code == "artifact_svg_active_content"
    with pytest.raises(AgentArtifactValidationError) as external:
        validate_agent_artifact_content(
            name="chart.svg",
            media_type="image/svg+xml",
            content=b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil/x"/></svg>',
        )
    assert external.value.code == "artifact_svg_external_reference"


@pytest.mark.parametrize(
    ("name", "media_type", "content"),
    [
        ("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0data\xff\xd9"),
        ("diagram.svg", "image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
        ("report.pdf", "application/pdf", b"%PDF-1.7\n%%EOF"),
        ("data.csv", "text/csv", "name,value\n论文,1\n".encode()),
        ("notes.md", "text/markdown", "# 研究笔记".encode()),
        ("notes.txt", "text/plain", "研究笔记".encode()),
        ("plot.py", "text/x-python", b"print('quadratic')\n"),
        ("data.json", "application/json", b'{"value":1}'),
    ],
)
def test_artifact_content_accepts_each_fixed_supported_type(
    name: str, media_type: str, content: bytes
) -> None:
    assert validate_agent_artifact_content(
        name=name, media_type=media_type, content=content
    ).size_bytes == len(content)


@pytest.mark.parametrize(
    ("name", "media_type", "content", "code"),
    [
        ("script.sh", "application/x-sh", b"#!/bin/sh", "artifact_media_type_unsupported"),
        (
            "chart.jpg",
            "image/png",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
            "artifact_extension_mismatch",
        ),
        ("data.json", "application/json", b"{", "artifact_json_invalid"),
        ("notes.txt", "text/plain", b"bad\x00text", "artifact_text_invalid"),
        ("plot.py", "text/plain", b"print(1)\n", "artifact_extension_mismatch"),
        (
            "chart.svg",
            "image/svg+xml",
            b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import "https://evil/x";</style></svg>',
            "artifact_svg_active_content",
        ),
        (
            "chart.svg",
            "image/svg+xml",
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="evil()"/></svg>',
            "artifact_svg_active_content",
        ),
    ],
)
def test_artifact_content_rejects_type_mismatch_or_active_content(
    name: str, media_type: str, content: bytes, code: str
) -> None:
    with pytest.raises(AgentArtifactValidationError) as caught:
        validate_agent_artifact_content(name=name, media_type=media_type, content=content)
    assert caught.value.code == code


def test_artifact_content_rejects_file_above_ten_mib() -> None:
    with pytest.raises(AgentArtifactValidationError) as caught:
        validate_agent_artifact_content(
            name="notes.txt",
            media_type="text/plain",
            content=b"x" * (AGENT_ARTIFACT_MAX_BYTES + 1),
        )
    assert caught.value.code == "artifact_too_large"


def test_artifact_identity_and_storage_key_are_stable_and_scoped() -> None:
    assert agent_artifact_candidate_id("turn-1", "call-1") == agent_artifact_candidate_id(
        "turn-1", "call-1"
    )
    key = agent_artifact_storage_key(
        owner_id="owner-1",
        session_id="session-1",
        turn_run_id="turn-1",
        content_hash="a" * 64,
    )
    assert key == "agent-artifacts/owner-1/session-1/turn-1/staging/" + "a" * 64


def test_only_committed_candidate_creates_immutable_agent_artifact() -> None:
    staged = create_agent_artifact_candidate(
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
        source_url="https://arxiv.org/pdf/2401.00001",
        source_url_hash="b" * 64,
    )
    with pytest.raises(ValueError, match="COMMITTED"):
        create_agent_artifact(candidate=staged)
    committed = staged.validate(
        tool_call_id="call-1",
        storage_key="agent-artifacts/owner-1/session-1/turn-1/staging/hash",
        sandbox_generation=1,
        sandbox_fencing_token=1,
    ).commit()
    artifact = create_agent_artifact(candidate=committed)
    duplicate = create_agent_artifact(candidate=committed)

    assert artifact.artifact_id == duplicate.artifact_id
    assert artifact.previewable is True
    assert artifact.source_url == "https://arxiv.org/pdf/2401.00001"
    assert artifact.source_url_hash == "b" * 64
    with pytest.raises(FrozenInstanceError):
        artifact.name = "changed.png"  # type: ignore[misc]
