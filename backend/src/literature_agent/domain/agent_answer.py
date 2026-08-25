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
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")


class AgentAnswerContractError(ValueError):
    """Agent 最终回答不符合可确定性验证的引用语法。"""


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
