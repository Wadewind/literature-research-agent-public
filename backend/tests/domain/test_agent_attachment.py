from datetime import UTC, datetime

import pytest

from literature_agent.domain.agent_attachment import (
    AgentAttachmentStatus,
    AgentAttachmentValidationError,
    agent_attachment_inbox_path,
    agent_attachment_request_hash,
    create_agent_attachment,
    validate_agent_attachment_content,
)


def test_attachment_identity_is_stable_and_content_is_immutable() -> None:
    content = b"name,value\na,1\n"
    validated = validate_agent_attachment_content(
        display_name="data.csv", media_type="text/csv", content=content
    )
    request_hash = agent_attachment_request_hash(
        session_id="session-1",
        display_name=validated.display_name,
        media_type=validated.media_type,
        content_hash=validated.content_hash,
    )
    first = create_agent_attachment(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        display_name=validated.display_name,
        media_type=validated.media_type,
        content_hash=validated.content_hash,
        size_bytes=validated.size_bytes,
        idempotency_key="upload-1",
        request_hash=request_hash,
    )
    second = create_agent_attachment(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        display_name=validated.display_name,
        media_type=validated.media_type,
        content_hash=validated.content_hash,
        size_bytes=validated.size_bytes,
        idempotency_key="upload-1",
        request_hash=request_hash,
    )

    assert first.attachment_id == second.attachment_id
    assert first.storage_key == second.storage_key
    deleted = first.delete(now=datetime(2026, 8, 28, tzinfo=UTC))
    assert deleted.status is AgentAttachmentStatus.DELETED
    assert deleted.content_hash == first.content_hash
    assert deleted.delete() == deleted


@pytest.mark.parametrize(
    ("name", "media_type", "content", "code"),
    [
        ("../data.csv", "text/csv", b"a,b\n1,2\n", "attachment_name_invalid"),
        ("data.json", "application/json", b"not-json", "attachment_json_invalid"),
        ("data.txt", "text/plain", b"\xff", "attachment_text_invalid"),
        ("paper.pdf", "application/pdf", b"not-pdf", "attachment_magic_mismatch"),
    ],
)
def test_attachment_validation_rejects_untrusted_content(
    name: str, media_type: str, content: bytes, code: str
) -> None:
    with pytest.raises(AgentAttachmentValidationError) as exc:
        validate_agent_attachment_content(
            display_name=name, media_type=media_type, content=content
        )
    assert exc.value.code == code


def test_inbox_path_uses_opaque_id_and_safe_name() -> None:
    assert agent_attachment_inbox_path("attachment-1", "data.csv") == (
        "/workspace/inbox/attachment-1/data.csv"
    )
    with pytest.raises(AgentAttachmentValidationError):
        agent_attachment_inbox_path("attachment-1", "../data.csv")
