"""Phase 3 结构化大纲与反馈的确定性校验。"""

import json
import re
from dataclasses import dataclass

MAX_OUTLINE_BYTES = 64 * 1024
MAX_SECTIONS = 12
MAX_TITLE_CHARS = 200
MAX_PURPOSE_CHARS = 1_000
MAX_FEEDBACK_CHARS = 4_000
_SECTION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class OutlineSection:
    """一个只引用 Search Strategy 维度的稳定大纲章节。"""

    section_key: str
    title: str
    purpose: str
    dimension_keys: tuple[str, ...]

    def to_payload(self) -> dict:
        return {
            "section_key": self.section_key,
            "title": self.title,
            "purpose": self.purpose,
            "dimension_keys": list(self.dimension_keys),
        }


@dataclass(frozen=True, slots=True)
class ReviewOutline:
    """通过 ``outline.v1`` 校验的不可变大纲。"""

    sections: tuple[OutlineSection, ...]

    def to_payload(self) -> dict:
        return {"sections": [section.to_payload() for section in self.sections]}


@dataclass(frozen=True, slots=True)
class OutlineValidationIssue:
    """可稳定返回给修复调用或 API 的校验问题。"""

    code: str
    path: str


class OutlineValidationError(ValueError):
    """大纲或反馈不满足确定性契约。"""

    def __init__(self, *issues: OutlineValidationIssue):
        self.issues = issues or (OutlineValidationIssue("outline_schema_invalid", "$"),)
        super().__init__(",".join(issue.code for issue in self.issues))


def parse_outline_json(content: str) -> ReviewOutline:
    """解析模型 JSON；归属维度由调用方随后校验。"""
    if len(content.encode()) > MAX_OUTLINE_BYTES:
        raise OutlineValidationError(OutlineValidationIssue("outline_output_too_large", "$"))
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise OutlineValidationError(OutlineValidationIssue("outline_json_invalid", "$")) from exc
    return validate_outline(payload, allowed_dimension_keys=None)


def validate_outline(
    payload: object,
    *,
    allowed_dimension_keys: tuple[str, ...] | list[str] | set[str] | None,
) -> ReviewOutline:
    """校验严格 Schema、稳定 key、维度闭包、文本和整体载荷大小。"""
    if not isinstance(payload, dict) or set(payload) != {"sections"}:
        raise OutlineValidationError(OutlineValidationIssue("outline_schema_invalid", "$"))
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise OutlineValidationError(OutlineValidationIssue("outline_schema_invalid", "$")) from exc
    if len(encoded) > MAX_OUTLINE_BYTES:
        raise OutlineValidationError(OutlineValidationIssue("outline_output_too_large", "$"))
    raw_sections = payload.get("sections")
    if (
        not isinstance(raw_sections, list)
        or isinstance(raw_sections, (str, bytes))
        or not 1 <= len(raw_sections) <= MAX_SECTIONS
    ):
        raise OutlineValidationError(
            OutlineValidationIssue("outline_section_count_invalid", "$.sections")
        )
    allowed = set(allowed_dimension_keys) if allowed_dimension_keys is not None else None
    sections: list[OutlineSection] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_sections):
        path = f"$.sections[{index}]"
        required = {"section_key", "title", "purpose", "dimension_keys"}
        if not isinstance(raw, dict) or set(raw) != required:
            raise OutlineValidationError(OutlineValidationIssue("outline_schema_invalid", path))
        key = raw["section_key"]
        if not isinstance(key, str) or not _SECTION_KEY_PATTERN.fullmatch(key):
            raise OutlineValidationError(
                OutlineValidationIssue("outline_section_key_invalid", f"{path}.section_key")
            )
        if key in seen:
            raise OutlineValidationError(
                OutlineValidationIssue("outline_section_key_duplicate", f"{path}.section_key")
            )
        seen.add(key)
        title = raw["title"]
        purpose = raw["purpose"]
        if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
            raise OutlineValidationError(
                OutlineValidationIssue("outline_title_invalid", f"{path}.title")
            )
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > MAX_PURPOSE_CHARS:
            raise OutlineValidationError(
                OutlineValidationIssue("outline_purpose_invalid", f"{path}.purpose")
            )
        dimension_keys = raw["dimension_keys"]
        if (
            not isinstance(dimension_keys, list)
            or not 1 <= len(dimension_keys) <= 6
            or any(not isinstance(item, str) or not item for item in dimension_keys)
            or len(set(dimension_keys)) != len(dimension_keys)
        ):
            raise OutlineValidationError(
                OutlineValidationIssue("outline_dimension_invalid", f"{path}.dimension_keys")
            )
        if allowed is not None and not set(dimension_keys).issubset(allowed):
            raise OutlineValidationError(
                OutlineValidationIssue("outline_dimension_invalid", f"{path}.dimension_keys")
            )
        sections.append(
            OutlineSection(
                section_key=key,
                title=title.strip(),
                purpose=purpose.strip(),
                dimension_keys=tuple(dimension_keys),
            )
        )
    return ReviewOutline(tuple(sections))


def validate_feedback(value: object) -> str:
    """校验人工反馈为非空、有界纯文本。"""
    if not isinstance(value, str):
        raise OutlineValidationError(
            OutlineValidationIssue("outline_feedback_invalid", "$.feedback")
        )
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_FEEDBACK_CHARS:
        raise OutlineValidationError(
            OutlineValidationIssue("outline_feedback_invalid", "$.feedback")
        )
    return normalized
