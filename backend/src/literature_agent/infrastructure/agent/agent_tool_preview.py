"""为产品界面生成有界、脱敏的 Agent Tool 输入输出预览。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_REDACTED = "[已脱敏]"
_TRUNCATED = "\n…（预览已截断）"
_EMPTY = "（无内容）"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|passwd|secret|cookie|private[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
_INLINE_CREDENTIAL = re.compile(
    r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|passwd|secret|cookie)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_MAX_COLLECTION_ITEMS = 80
_MAX_DEPTH = 8


@dataclass(frozen=True, slots=True)
class AgentToolPreview:
    text: str
    truncated: bool


def build_tool_preview(
    value: object,
    *,
    max_bytes: int,
    pretty: bool = False,
) -> AgentToolPreview:
    """生成可公开预览；敏感键和常见行内凭据先脱敏，再按 UTF-8 字节截断。"""
    if max_bytes <= len(_TRUNCATED.encode("utf-8")):
        raise ValueError("Tool 预览字节上限过小")
    safe_value = _redact(value, depth=0)
    if isinstance(safe_value, str):
        rendered = safe_value
    else:
        rendered = json.dumps(
            safe_value,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
    rendered = _redact_inline(rendered).strip() or _EMPTY
    encoded = rendered.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return AgentToolPreview(rendered, False)
    marker = _TRUNCATED.encode("utf-8")
    prefix = encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore")
    return AgentToolPreview(f"{prefix}{_TRUNCATED}", True)


def _redact(value: object, *, depth: int) -> object:
    if depth >= _MAX_DEPTH:
        return "[结构过深，已省略]"
    if isinstance(value, Mapping):
        mapping_result: dict[str, object] = {}
        for index, (key, nested) in enumerate(value.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                mapping_result["…"] = "[其余字段已省略]"
                break
            key_text = str(key)
            mapping_result[key_text] = (
                _REDACTED
                if _SENSITIVE_KEY.search(key_text)
                else _redact(nested, depth=depth + 1)
            )
        return mapping_result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence_result = [
            _redact(item, depth=depth + 1) for item in value[:_MAX_COLLECTION_ITEMS]
        ]
        if len(value) > _MAX_COLLECTION_ITEMS:
            sequence_result.append("[其余项目已省略]")
        return sequence_result
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return _redact_inline(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_inline(str(value))


def _redact_inline(value: str) -> str:
    value = _BEARER_TOKEN.sub(f"Bearer {_REDACTED}", value)
    value = _INLINE_CREDENTIAL.sub(lambda match: f"{match.group(1)}={_REDACTED}", value)
    return value
