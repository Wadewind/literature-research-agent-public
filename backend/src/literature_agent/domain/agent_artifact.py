"""Research Agent 正式文件产物与内容策略。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, uuid5

from literature_agent.domain.research_agent import (
    AgentArtifactCandidate,
    AgentArtifactCandidateStatus,
)
from literature_agent.domain.workspace_snapshot import WORKSPACE_MAX_FILE_BYTES

AGENT_ARTIFACT_OUTPUT_ROOT = "/workspace/outputs"
AGENT_ARTIFACT_MAX_BYTES = WORKSPACE_MAX_FILE_BYTES

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[^/\\\x00\r\n]{1,255}$")


class AgentArtifactMediaType(StrEnum):
    """正式 Agent Artifact 允许声明的固定媒体类型。"""

    PNG = "image/png"
    JPEG = "image/jpeg"
    SVG = "image/svg+xml"
    PDF = "application/pdf"
    CSV = "text/csv"
    MARKDOWN = "text/markdown"
    TEXT = "text/plain"
    JSON = "application/json"
    PYTHON = "text/x-python"


_SUPPORTED_TYPES: dict[str, tuple[str, ...]] = {
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/svg+xml": (".svg",),
    "application/pdf": (".pdf",),
    "text/csv": (".csv",),
    "text/markdown": (".md", ".markdown"),
    "text/plain": (".txt",),
    "application/json": (".json",),
    "text/x-python": (".py",),
}
_SVG_FORBIDDEN_ELEMENTS = {
    "script",
    "style",
    "foreignobject",
    "iframe",
    "object",
    "embed",
    "audio",
    "video",
    "animate",
    "animatemotion",
    "animatetransform",
    "set",
}
_SVG_EXTERNAL_VALUE = re.compile(
    r"(?:^|[\s(\"'])\s*(?:https?:|file:|data:|//)|url\s*\(", re.IGNORECASE
)


class AgentArtifactValidationError(ValueError):
    """不可信 Sandbox 文件未通过正式产物边界。"""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


def agent_artifact_supported_types() -> tuple[dict[str, object], ...]:
    """返回可安全写入 Tool 错误的媒体类型与扩展名清单。"""
    return tuple(
        {"media_type": media_type, "extensions": extensions}
        for media_type, extensions in _SUPPORTED_TYPES.items()
    )


def agent_artifact_supported_type_hint() -> str:
    """返回供受限 Agent Prompt 使用的紧凑类型提示。"""
    return "，".join(
        f"{'/'.join(extensions)}={media_type}"
        for media_type, extensions in _SUPPORTED_TYPES.items()
    )


@dataclass(frozen=True, slots=True)
class AgentArtifact:
    """绑定当前 Turn、不可原地覆盖的正式 Agent 产物。"""

    artifact_id: str
    candidate_id: str
    owner_id: str
    project_id: str
    session_id: str
    turn_run_id: str
    name: str
    media_type: str
    content_hash: str
    size_bytes: int
    storage_key: str
    created_at: datetime
    source_url: str | None = None
    source_url_hash: str | None = None

    def __post_init__(self) -> None:
        values = (
            self.artifact_id,
            self.candidate_id,
            self.owner_id,
            self.project_id,
            self.session_id,
            self.turn_run_id,
            self.storage_key,
        )
        if not all(value.strip() for value in values):
            raise ValueError("AgentArtifact 作用域与存储引用不能为空")
        validate_agent_artifact_name_and_type(self.name, self.media_type)
        if not _SHA256.fullmatch(self.content_hash):
            raise ValueError("AgentArtifact content_hash 必须是小写 SHA-256")
        if not 0 <= self.size_bytes <= AGENT_ARTIFACT_MAX_BYTES:
            raise ValueError("AgentArtifact 文件大小超过 10 MiB")
        if (self.source_url is None) != (self.source_url_hash is None):
            raise ValueError("AgentArtifact source 引用必须完整")
        if self.source_url_hash is not None and not _SHA256.fullmatch(self.source_url_hash):
            raise ValueError("AgentArtifact source hash 非法")

    @property
    def previewable(self) -> bool:
        """首版只允许浏览器直接预览不含主动内容的位图。"""
        return self.media_type in {"image/png", "image/jpeg"}


@dataclass(frozen=True, slots=True)
class ValidatedAgentArtifactContent:
    """已经过扩展名、声明 MIME、magic 与结构校验的内容。"""

    name: str
    media_type: str
    content_hash: str
    size_bytes: int


def is_agent_artifact_output_path(path: str) -> bool:
    """只接受规范化 `/workspace/outputs/` 子路径。"""
    if not path.startswith(f"{AGENT_ARTIFACT_OUTPUT_ROOT}/"):
        return False
    if "//" in path or path.endswith("/") or "\x00" in path:
        return False
    normalized = posixpath.normpath(path)
    return normalized == path and normalized != AGENT_ARTIFACT_OUTPUT_ROOT


def validate_agent_artifact_name_and_type(name: str, media_type: str) -> None:
    """校验公开文件名、声明媒体类型和扩展名的固定组合。"""
    if not _SAFE_NAME.fullmatch(name) or name in {".", ".."}:
        raise AgentArtifactValidationError("artifact_name_invalid", "Artifact 文件名非法")
    extensions = _SUPPORTED_TYPES.get(media_type)
    if extensions is None:
        raise AgentArtifactValidationError(
            "artifact_media_type_unsupported", "Artifact 媒体类型不受支持"
        )
    if PurePosixPath(name).suffix.lower() not in extensions:
        raise AgentArtifactValidationError(
            "artifact_extension_mismatch", "Artifact 扩展名与媒体类型不匹配"
        )


def validate_agent_artifact_content(
    *, name: str, media_type: str, content: bytes
) -> ValidatedAgentArtifactContent:
    """使用标准库完成首版有界内容验证，不执行或渲染输入。"""
    validate_agent_artifact_name_and_type(name, media_type)
    if len(content) > AGENT_ARTIFACT_MAX_BYTES:
        raise AgentArtifactValidationError("artifact_too_large", "Artifact 文件超过 10 MiB")
    if media_type == "image/png":
        if len(content) < 24 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AgentArtifactValidationError("artifact_magic_mismatch", "PNG 文件头非法")
    elif media_type == "image/jpeg":
        if (
            len(content) < 4
            or not content.startswith(b"\xff\xd8\xff")
            or not content.endswith(b"\xff\xd9")
        ):
            raise AgentArtifactValidationError("artifact_magic_mismatch", "JPEG 文件结构非法")
    elif media_type == "application/pdf":
        if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
            raise AgentArtifactValidationError("artifact_magic_mismatch", "PDF 文件结构非法")
    elif media_type == "image/svg+xml":
        _validate_svg(content)
    else:
        text = _decode_text(content)
        if media_type == "application/json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise AgentArtifactValidationError(
                    "artifact_json_invalid", "JSON 文件结构非法"
                ) from exc
        elif media_type == "text/csv":
            try:
                list(csv.reader(io.StringIO(text), strict=True))
            except csv.Error as exc:
                raise AgentArtifactValidationError(
                    "artifact_csv_invalid", "CSV 文件结构非法"
                ) from exc
    return ValidatedAgentArtifactContent(
        name=name,
        media_type=media_type,
        content_hash=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def agent_artifact_candidate_id(turn_run_id: str, tool_call_id: str) -> str:
    """同一 Turn 的同一 Tool call 稳定映射到一个 Candidate。"""
    if not turn_run_id.strip() or not tool_call_id.strip():
        raise ValueError("Turn 与 Tool call ID 不能为空")
    return f"candidate:{uuid5(NAMESPACE_URL, f'agent-artifact:{turn_run_id}:{tool_call_id}')}"


def agent_artifact_storage_key(
    *, owner_id: str, session_id: str, turn_run_id: str, content_hash: str
) -> str:
    """构造 owner/Session/Turn 隔离且内容寻址的 staging key。"""
    if not all(value.strip() for value in (owner_id, session_id, turn_run_id)):
        raise ValueError("Artifact staging scope 不能为空")
    if not _SHA256.fullmatch(content_hash):
        raise ValueError("Artifact staging hash 非法")
    return f"agent-artifacts/{owner_id}/{session_id}/{turn_run_id}/staging/{content_hash}"


def create_agent_artifact(*, candidate: AgentArtifactCandidate) -> AgentArtifact:
    """从已 COMMITTED Candidate 生成稳定、不可变正式产物。"""
    if candidate.status is not AgentArtifactCandidateStatus.COMMITTED:
        raise ValueError("只有 COMMITTED Candidate 可以创建 AgentArtifact")
    storage_key = candidate.storage_key
    if not isinstance(storage_key, str) or not storage_key:
        raise ValueError("COMMITTED Candidate 缺少 staging storage key")
    candidate_id = candidate.candidate_id
    return AgentArtifact(
        artifact_id=str(uuid5(NAMESPACE_URL, f"agent-artifact:{candidate_id}")),
        candidate_id=candidate_id,
        owner_id=candidate.owner_id,
        project_id=candidate.project_id,
        session_id=candidate.session_id,
        turn_run_id=candidate.turn_run_id,
        name=candidate.name,
        media_type=candidate.media_type,
        content_hash=candidate.content_hash,
        size_bytes=candidate.size_bytes,
        storage_key=storage_key,
        created_at=datetime.now(UTC),
        source_url=candidate.source_url,
        source_url_hash=candidate.source_url_hash,
    )


def _decode_text(content: bytes) -> str:
    if b"\x00" in content:
        raise AgentArtifactValidationError("artifact_text_invalid", "文本文件包含 NUL")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentArtifactValidationError(
            "artifact_text_invalid", "文本文件必须使用 UTF-8"
        ) from exc


def _validate_svg(content: bytes) -> None:
    lowered = content.lower()
    if (
        b"<!doctype" in lowered
        or b"<!entity" in lowered
        or b"<?xml-stylesheet" in lowered
    ):
        raise AgentArtifactValidationError("artifact_svg_active_content", "SVG 含主动内容")
    text = _decode_text(content)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise AgentArtifactValidationError("artifact_svg_invalid", "SVG XML 结构非法") from exc
    if _local_name(root.tag) != "svg":
        raise AgentArtifactValidationError("artifact_svg_invalid", "SVG 根元素非法")
    for element in root.iter():
        if _local_name(element.tag).lower() in _SVG_FORBIDDEN_ELEMENTS:
            raise AgentArtifactValidationError("artifact_svg_active_content", "SVG 含主动内容")
        for raw_name, value in element.attrib.items():
            name = _local_name(raw_name).lower()
            normalized = value.strip()
            if name.startswith("on") or name in {"src", "formaction"}:
                raise AgentArtifactValidationError("artifact_svg_active_content", "SVG 含主动内容")
            if (
                (name == "href" and normalized and not normalized.startswith("#"))
                or _SVG_EXTERNAL_VALUE.search(normalized)
                or "@import" in normalized.lower()
                or "expression(" in normalized.lower()
            ):
                raise AgentArtifactValidationError(
                    "artifact_svg_external_reference", "SVG 含外部引用"
                )


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]
