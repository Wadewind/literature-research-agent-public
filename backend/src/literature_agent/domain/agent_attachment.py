"""Research Agent 输入附件及其受控文件策略。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from literature_agent.domain.agent_artifact import (
    AGENT_ARTIFACT_MAX_BYTES,
    AgentArtifactValidationError,
    validate_agent_artifact_content,
)

AGENT_ATTACHMENT_MAX_BYTES = AGENT_ARTIFACT_MAX_BYTES
AGENT_MESSAGE_MAX_ATTACHMENTS = 5
AGENT_ATTACHMENT_INBOX_ROOT = "/workspace/inbox"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AgentAttachmentStatus(StrEnum):
    """附件只允许从可用状态转为不可用，不原地覆盖内容。"""

    AVAILABLE = "available"
    DELETED = "deleted"


class AgentAttachmentValidationError(ValueError):
    """上传内容未通过受限研究文件策略。"""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class AgentAttachment:
    """owner/Project/Session scoped、内容不可变的用户输入文件。"""

    attachment_id: str
    owner_id: str
    project_id: str
    session_id: str
    version: int
    display_name: str
    media_type: str
    content_hash: str
    size_bytes: int
    storage_key: str
    idempotency_key: str
    request_hash: str
    status: AgentAttachmentStatus
    created_at: datetime
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        values = (
            self.attachment_id,
            self.owner_id,
            self.project_id,
            self.session_id,
            self.storage_key,
            self.idempotency_key,
        )
        if not all(value.strip() for value in values):
            raise ValueError("AgentAttachment 作用域和内部引用不能为空")
        if self.version != 1:
            raise ValueError("AgentAttachment 首版只允许不可变 version=1")
        if not _SHA256.fullmatch(self.content_hash) or not _SHA256.fullmatch(
            self.request_hash
        ):
            raise ValueError("AgentAttachment hash 必须是小写 SHA-256")
        if not 0 <= self.size_bytes <= AGENT_ATTACHMENT_MAX_BYTES:
            raise ValueError("AgentAttachment 文件大小必须在 0..10_MiB 范围内")
        _validate_name_and_type(self.display_name, self.media_type)
        if self.status is AgentAttachmentStatus.AVAILABLE and self.deleted_at is not None:
            raise ValueError("AVAILABLE AgentAttachment 不能携带 deleted_at")
        if self.status is AgentAttachmentStatus.DELETED and self.deleted_at is None:
            raise ValueError("DELETED AgentAttachment 必须携带 deleted_at")

    def delete(self, *, now: datetime | None = None) -> AgentAttachment:
        """把未引用附件幂等标记为不可用；内容身份保持不变。"""
        if self.status is AgentAttachmentStatus.DELETED:
            return self
        return replace(
            self,
            status=AgentAttachmentStatus.DELETED,
            deleted_at=now or datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class ValidatedAgentAttachmentContent:
    """上传边界验证后的附件元数据。"""

    display_name: str
    media_type: str
    content_hash: str
    size_bytes: int


def validate_agent_attachment_content(
    *, display_name: str, media_type: str, content: bytes
) -> ValidatedAgentAttachmentContent:
    """复用已固定的研究文件类型/magic/结构策略，并转换为附件错误。"""
    try:
        validated = validate_agent_artifact_content(
            name=display_name,
            media_type=media_type,
            content=content,
        )
    except AgentArtifactValidationError as exc:
        code = exc.code.replace("artifact_", "attachment_", 1)
        raise AgentAttachmentValidationError(
            code,
            exc.safe_message.replace("Artifact", "附件"),
        ) from exc
    return ValidatedAgentAttachmentContent(
        display_name=validated.name,
        media_type=validated.media_type,
        content_hash=validated.content_hash,
        size_bytes=validated.size_bytes,
    )


def create_agent_attachment(
    *,
    owner_id: str,
    project_id: str,
    session_id: str,
    display_name: str,
    media_type: str,
    content_hash: str,
    size_bytes: int,
    idempotency_key: str,
    request_hash: str,
) -> AgentAttachment:
    """由 owner/Session/幂等键稳定派生附件与内部 Storage key。"""
    if not all(value.strip() for value in (owner_id, project_id, session_id)):
        raise ValueError("AgentAttachment scope 不能为空")
    if not idempotency_key.strip() or len(idempotency_key) > 255:
        raise ValueError("Idempotency-Key 不能为空且长度不得超过 255")
    attachment_id = str(
        uuid5(NAMESPACE_URL, f"agent-attachment:{owner_id}:{session_id}:{idempotency_key}")
    )
    storage_key = (
        f"agent-attachments/{owner_id}/{session_id}/{attachment_id}/{content_hash}"
    )
    return AgentAttachment(
        attachment_id=attachment_id,
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        version=1,
        display_name=display_name,
        media_type=media_type,
        content_hash=content_hash,
        size_bytes=size_bytes,
        storage_key=storage_key,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status=AgentAttachmentStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )


def agent_attachment_request_hash(
    *, session_id: str, display_name: str, media_type: str, content_hash: str
) -> str:
    """上传幂等指纹覆盖作用域、公开元数据与内容身份。"""
    payload = {
        "session_id": session_id,
        "display_name": display_name,
        "media_type": media_type,
        "content_hash": content_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def agent_attachment_inbox_path(attachment_id: str, display_name: str) -> str:
    """生成只由已验证业务事实决定的 Sandbox inbox 路径。"""
    if (
        not attachment_id.strip()
        or len(attachment_id) > 64
        or any(char in attachment_id for char in ("/", "\\", "\x00", "\r", "\n"))
        or attachment_id in {".", ".."}
    ):
        raise ValueError("attachment_id 非法")
    _validate_name_and_type(display_name, "text/plain", validate_type=False)
    return f"{AGENT_ATTACHMENT_INBOX_ROOT}/{attachment_id}/{display_name}"


def is_agent_attachment_inbox_path(path: str) -> bool:
    """判断物理 Workspace 路径是否属于每轮授权的 inbox。"""
    return path == AGENT_ATTACHMENT_INBOX_ROOT or path.startswith(
        f"{AGENT_ATTACHMENT_INBOX_ROOT}/"
    )


def _validate_name_and_type(
    display_name: str, media_type: str, *, validate_type: bool = True
) -> None:
    try:
        # 内容为空也会触发部分 magic 校验，因此这里只使用稳定的名称/类型入口。
        from literature_agent.domain.agent_artifact import (
            validate_agent_artifact_name_and_type,
        )

        if validate_type:
            validate_agent_artifact_name_and_type(display_name, media_type)
        elif (
            not display_name
            or len(display_name) > 255
            or any(char in display_name for char in ("/", "\\", "\x00", "\r", "\n"))
            or display_name in {".", ".."}
        ):
            raise AgentArtifactValidationError(
                "artifact_name_invalid", "Artifact 文件名非法"
            )
    except AgentArtifactValidationError as exc:
        raise AgentAttachmentValidationError(
            exc.code.replace("artifact_", "attachment_", 1),
            exc.safe_message.replace("Artifact", "附件"),
        ) from exc
