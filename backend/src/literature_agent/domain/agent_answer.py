"""Research Agent 最终回答的严格行内 Evidence 标记契约。"""

import re

from literature_agent.domain.answer_schema import ClaimDraft, RagAnswerOutput
from literature_agent.domain.evidence import AnswerStatus

INSUFFICIENT_AGENT_EVIDENCE_TEXT = "当前授权上下文证据不足。"
AGENT_ANSWER_MAX_CHARS = 12_000
AGENT_ANSWER_MAX_CLAIMS = 32
AGENT_CLAIM_MAX_CHARS = 2_000
AGENT_CLAIM_MAX_EVIDENCE = 10
_CLAIM_LINE = re.compile(r"^(?P<text>.+?) \[evidence:(?P<ids>[^\[\]]+)\]$")
_TRAILING_EVIDENCE = re.compile(r"^(?P<text>.+?)\s*\[evidence:(?P<ids>[^\[\]]+)\]$")
_EVIDENCE_MARKER = re.compile(r"\[evidence:(?P<ids>[^\[\]]+)\]")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_MARKDOWN_PREFIX = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")
_EVIDENCE_PLACEHOLDER = re.compile(
    r"\[evidence:\s*(?:…|\.{3}|<id>|<id>\s*\[,\s*<id>\s*(?:…|\.{3})\])\s*\]"
)


class AgentAnswerContractError(ValueError):
    """Agent 最终回答不符合可确定性验证的引用语法。"""


def normalize_agent_evidence_placeholders(content: str) -> str:
    """只中和明显的 Evidence 格式占位符，不放宽真实引用校验。"""
    return _EVIDENCE_PLACEHOLDER.sub("Evidence 标记", content)


def normalize_agent_evidence_markup(content: str) -> str:
    """确定性整理等价 Evidence 排版；有歧义的行保持原样交给严格校验拒绝。"""
    placeholder_safe = normalize_agent_evidence_placeholders(content)
    return "\n".join(
        _normalize_agent_evidence_line(line) for line in placeholder_safe.splitlines()
    )


def _normalize_agent_evidence_line(line: str) -> str:
    if "[evidence:" not in line:
        return line
    matches = list(_EVIDENCE_MARKER.finditer(line))
    if not matches:
        return line

    first = matches[0]
    claim_text = line[: first.start()].strip()
    evidence_ids = _normalized_evidence_ids(first.group("ids"))
    if not claim_text or evidence_ids is None:
        return line

    claims: list[tuple[str, list[str]]] = []
    previous = first
    for marker in matches[1:]:
        between = line[previous.end() : marker.start()].strip()
        marker_ids = _normalized_evidence_ids(marker.group("ids"))
        if marker_ids is None:
            return line
        if between:
            claims.append((claim_text, evidence_ids))
            claim_text = between
            evidence_ids = marker_ids
        else:
            evidence_ids = _deduplicated((*evidence_ids, *marker_ids))
            if len(evidence_ids) > AGENT_CLAIM_MAX_EVIDENCE:
                return line
        previous = marker

    if line[previous.end() :].strip():
        return line
    claims.append((claim_text, evidence_ids))
    if len(claims) > AGENT_ANSWER_MAX_CLAIMS:
        return line
    return "\n".join(
        f"{text} [evidence:{','.join(ids)}]" for text, ids in claims
    )


def _normalized_evidence_ids(raw_ids: str) -> list[str] | None:
    evidence_ids = [item.strip() for item in raw_ids.split(",")]
    if (
        not evidence_ids
        or any(not _EVIDENCE_ID.fullmatch(item) for item in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
    ):
        return None
    return evidence_ids if len(evidence_ids) <= AGENT_CLAIM_MAX_EVIDENCE else None


def _deduplicated(values: tuple[str, ...] | list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def canonicalize_agent_answer(content: str) -> str:
    """从 Runtime 富文本中只保留可验证 Claim，并规范为产品消息契约。"""
    normalized = normalize_agent_evidence_markup(content).strip()
    if len(content) > AGENT_ANSWER_MAX_CHARS:
        raise AgentAnswerContractError("Agent 回答超过字符上限")
    if normalized == INSUFFICIENT_AGENT_EVIDENCE_TEXT:
        return normalized

    claims: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if "[evidence:" not in line:
            continue
        matched = _TRAILING_EVIDENCE.fullmatch(line)
        if matched is None:
            continue
        text = _MARKDOWN_PREFIX.sub("", matched.group("text").strip(), count=1).strip()
        evidence_ids = [item.strip() for item in matched.group("ids").split(",")]
        if (
            not text
            or len(text) > AGENT_CLAIM_MAX_CHARS
            or "[evidence:" in text
            or not evidence_ids
            or len(evidence_ids) > AGENT_CLAIM_MAX_EVIDENCE
            or any(not _EVIDENCE_ID.fullmatch(item) for item in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
        ):
            continue
        claims.append(f"{text} [evidence:{','.join(evidence_ids)}]")
        if len(claims) > AGENT_ANSWER_MAX_CLAIMS:
            raise AgentAnswerContractError("Agent Claim 数量超过上限")

    canonical = "\n".join(claims)
    if not canonical:
        raise AgentAnswerContractError("Agent 回答没有可验证的带引用论述")
    parse_agent_answer(canonical)
    return canonical


def extract_agent_evidence_claims(content: str) -> tuple[RagAnswerOutput, tuple[str, ...]]:
    """从混合富文本中提取显式引用 Claim，不改写未标记的模型正文。"""
    normalized = normalize_agent_evidence_markup(content).strip()
    if len(content) > AGENT_ANSWER_MAX_CHARS:
        raise AgentAnswerContractError("Agent 回答超过字符上限")
    if normalized == INSUFFICIENT_AGENT_EVIDENCE_TEXT:
        return parse_agent_answer(normalized)
    if not normalized:
        raise AgentAnswerContractError("Agent 回答不能为空")

    claims: list[ClaimDraft] = []
    ordered_ids: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if "[evidence:" not in line:
            continue
        matched = _TRAILING_EVIDENCE.fullmatch(line)
        if matched is None:
            raise AgentAnswerContractError("显式 Evidence 标记必须位于论述行末")
        text = _MARKDOWN_PREFIX.sub("", matched.group("text").strip(), count=1).strip()
        evidence_ids = [item.strip() for item in matched.group("ids").split(",")]
        if (
            not text
            or len(text) > AGENT_CLAIM_MAX_CHARS
            or "[evidence:" in text
            or not evidence_ids
            or len(evidence_ids) > AGENT_CLAIM_MAX_EVIDENCE
            or any(not _EVIDENCE_ID.fullmatch(item) for item in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
        ):
            raise AgentAnswerContractError("Agent Evidence 标记非法")
        claims.append(ClaimDraft(text=text, evidence_ids=evidence_ids))
        if len(claims) > AGENT_ANSWER_MAX_CLAIMS:
            raise AgentAnswerContractError("Agent Claim 数量超过上限")
        for evidence_id in evidence_ids:
            if evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)
    if not claims:
        raise AgentAnswerContractError("Agent 回答没有显式 Evidence Claim")
    return (
        RagAnswerOutput(answer_status=AnswerStatus.ANSWERED, claims=claims),
        tuple(ordered_ids),
    )


def parse_agent_answer(content: str) -> tuple[RagAnswerOutput, tuple[str, ...]]:
    """把逐行行内标记解析为既有 ``RagAnswerOutput`` 语义。"""
    normalized = content.strip()
    if len(content) > AGENT_ANSWER_MAX_CHARS:
        raise AgentAnswerContractError("Agent 回答超过字符上限")
    if normalized == INSUFFICIENT_AGENT_EVIDENCE_TEXT:
        return (
            RagAnswerOutput(
                answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                claims=[],
            ),
            (),
        )
    if not normalized:
        raise AgentAnswerContractError("Agent 回答不能为空")

    claims: list[ClaimDraft] = []
    ordered_ids: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = _CLAIM_LINE.fullmatch(line)
        if matched is None:
            raise AgentAnswerContractError("每条论述必须以行内 Evidence 标记结尾")
        text = matched.group("text").strip()
        evidence_ids = [item.strip() for item in matched.group("ids").split(",")]
        if (
            not text
            or len(text) > AGENT_CLAIM_MAX_CHARS
            or "[evidence:" in text
            or not evidence_ids
            or len(evidence_ids) > AGENT_CLAIM_MAX_EVIDENCE
            or any(not _EVIDENCE_ID.fullmatch(item) for item in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
        ):
            raise AgentAnswerContractError("Agent Evidence 标记非法")
        claims.append(ClaimDraft(text=text, evidence_ids=evidence_ids))
        if len(claims) > AGENT_ANSWER_MAX_CLAIMS:
            raise AgentAnswerContractError("Agent Claim 数量超过上限")
        for evidence_id in evidence_ids:
            if evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)
    if not claims:
        raise AgentAnswerContractError("Agent 回答必须包含至少一条论述")
    return (
        RagAnswerOutput(answer_status=AnswerStatus.ANSWERED, claims=claims),
        tuple(ordered_ids),
    )
