"""综述章节草稿与全文一致性报告的确定性契约。"""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from literature_agent.domain.evidence import AnswerStatus

SECTION_MAX_MODEL_OUTPUT_BYTES = 192 * 1024
CONSISTENCY_MAX_MODEL_OUTPUT_BYTES = 64 * 1024


class SectionDraftValidationError(ValueError):
    """章节模型输出不满足 ``section.v1``。"""


class ConsistencyReportValidationError(ValueError):
    """一致性模型输出不满足 ``consistency-report.v1``。"""


class _ClaimPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str]


class _TermPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    definition: str


class _SectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_key: str
    title: str
    status: AnswerStatus
    summary: str
    claims: list[_ClaimPayload]
    terminology: list[_TermPayload]


class _IssuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: Literal["terminology", "contradiction", "redundancy"]
    section_keys: list[str]
    description: str


class _ConsistencyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["consistent", "issues_found"]
    issues: list[_IssuePayload]


@dataclass(frozen=True, slots=True)
class SectionClaimDraft:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TerminologyEntry:
    term: str
    definition: str


@dataclass(frozen=True, slots=True)
class SectionDraft:
    section_key: str
    title: str
    status: AnswerStatus
    summary: str
    claims: tuple[SectionClaimDraft, ...]
    terminology: tuple[TerminologyEntry, ...]

    def to_payload(self) -> dict:
        return {
            "section_key": self.section_key,
            "title": self.title,
            "status": self.status.value,
            "summary": self.summary,
            "claims": [
                {"text": claim.text, "evidence_ids": list(claim.evidence_ids)}
                for claim in self.claims
            ],
            "terminology": [
                {"term": item.term, "definition": item.definition} for item in self.terminology
            ],
        }


@dataclass(frozen=True, slots=True)
class ConsistencyIssue:
    issue_type: str
    section_keys: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    status: str
    issues: tuple[ConsistencyIssue, ...]

    def to_payload(self) -> dict:
        return {
            "status": self.status,
            "issues": [
                {
                    "issue_type": issue.issue_type,
                    "section_keys": list(issue.section_keys),
                    "description": issue.description,
                }
                for issue in self.issues
            ],
        }


def parse_section_draft_json(content: str) -> _SectionPayload:
    if len(content.encode("utf-8")) > SECTION_MAX_MODEL_OUTPUT_BYTES:
        raise SectionDraftValidationError("章节模型输出超过大小上限")
    try:
        return _SectionPayload.model_validate_json(content)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise SectionDraftValidationError("章节输出不符合 section.v1 Schema") from exc


def validate_section_draft(
    payload: _SectionPayload,
    *,
    expected_section_key: str,
    expected_title: str,
    allowed_evidence_ids: set[str],
) -> SectionDraft:
    if payload.section_key != expected_section_key or payload.title != expected_title:
        raise SectionDraftValidationError("章节身份与批准大纲不一致")
    summary = payload.summary.strip()
    if not summary or len(summary) > 1_000:
        raise SectionDraftValidationError("章节摘要不能为空且不得超过 1000 字符")
    if len(payload.claims) > 50 or len(payload.terminology) > 50:
        raise SectionDraftValidationError("章节 Claim 或术语数量超限")
    if payload.status is AnswerStatus.ANSWERED and not payload.claims:
        raise SectionDraftValidationError("answered 章节必须包含 Claim")
    if payload.status is AnswerStatus.INSUFFICIENT_EVIDENCE and payload.claims:
        raise SectionDraftValidationError("证据不足章节不得包含 Claim")
    claims: list[SectionClaimDraft] = []
    for raw in payload.claims:
        text = raw.text.strip()
        ids = tuple(raw.evidence_ids)
        if not text or len(text) > 4_000:
            raise SectionDraftValidationError("Claim 文本不能为空且不得超过 4000 字符")
        if not ids or len(ids) > 10 or len(set(ids)) != len(ids):
            raise SectionDraftValidationError("Claim 必须绑定 1–10 个不重复 Evidence")
        if not set(ids) <= allowed_evidence_ids:
            raise SectionDraftValidationError("Claim 引用了章节上下文外 Evidence")
        claims.append(SectionClaimDraft(text, ids))
    terms: list[TerminologyEntry] = []
    seen_terms: set[str] = set()
    for raw in payload.terminology:
        term, definition = raw.term.strip(), raw.definition.strip()
        if (
            not term
            or len(term) > 100
            or not definition
            or len(definition) > 500
            or term in seen_terms
        ):
            raise SectionDraftValidationError("术语为空、重复或超过长度边界")
        seen_terms.add(term)
        terms.append(TerminologyEntry(term, definition))
    return SectionDraft(
        payload.section_key,
        payload.title,
        payload.status,
        summary,
        tuple(claims),
        tuple(terms),
    )


def parse_consistency_report_json(content: str) -> _ConsistencyPayload:
    if len(content.encode("utf-8")) > CONSISTENCY_MAX_MODEL_OUTPUT_BYTES:
        raise ConsistencyReportValidationError("一致性模型输出超过大小上限")
    try:
        return _ConsistencyPayload.model_validate_json(content)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise ConsistencyReportValidationError(
            "一致性输出不符合 consistency-report.v1 Schema"
        ) from exc


def validate_consistency_report(
    payload: _ConsistencyPayload,
    *,
    allowed_section_keys: tuple[str, ...],
) -> ConsistencyReport:
    allowed = set(allowed_section_keys)
    if len(payload.issues) > 50:
        raise ConsistencyReportValidationError("一致性问题数量超限")
    if payload.status == "consistent" and payload.issues:
        raise ConsistencyReportValidationError("consistent 报告不得包含问题")
    if payload.status == "issues_found" and not payload.issues:
        raise ConsistencyReportValidationError("issues_found 报告必须包含问题")
    issues: list[ConsistencyIssue] = []
    for raw in payload.issues:
        keys = tuple(raw.section_keys)
        description = raw.description.strip()
        if (
            not keys
            or len(keys) > 12
            or len(set(keys)) != len(keys)
            or not set(keys) <= allowed
            or not description
            or len(description) > 1_000
        ):
            raise ConsistencyReportValidationError("一致性问题范围或描述非法")
        issues.append(ConsistencyIssue(raw.issue_type, keys, description))
    return ConsistencyReport(payload.status, tuple(issues))


SECTION_JSON_SCHEMA = _SectionPayload.model_json_schema()
CONSISTENCY_JSON_SCHEMA = _ConsistencyPayload.model_json_schema()
