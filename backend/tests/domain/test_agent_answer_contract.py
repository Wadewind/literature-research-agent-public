"""Research Agent 行内 Evidence 标记契约。"""

import pytest

from literature_agent.domain.agent_answer import (
    AgentAnswerContractError,
    parse_agent_answer,
)
from literature_agent.domain.evidence import AnswerStatus


def test_parse_agent_answer_builds_existing_rag_claim_semantics() -> None:
    output, evidence_ids = parse_agent_answer(
        "方法提升了准确率 [evidence:e-1,e-2]\n"
        "但样本规模有限 [evidence:e-2]"
    )

    assert output.answer_status is AnswerStatus.ANSWERED
    assert [claim.text for claim in output.claims] == ["方法提升了准确率", "但样本规模有限"]
    assert [claim.evidence_ids for claim in output.claims] == [["e-1", "e-2"], ["e-2"]]
    assert evidence_ids == ("e-1", "e-2")


@pytest.mark.parametrize(
    "content",
    [
        "没有引用的论述",
        "论述 [evidence:]",
        "论述 [evidence:e-1,e-1]",
        "论述 [evidence:e-1] 后面还有内容",
        "论述 [evidence:e-1] 伪造尾注 [evidence:e-2]",
    ],
)
def test_parse_agent_answer_rejects_unmarked_or_malformed_claims(content: str) -> None:
    with pytest.raises(AgentAnswerContractError):
        parse_agent_answer(content)


def test_parse_agent_answer_allows_explicit_insufficient_evidence() -> None:
    output, evidence_ids = parse_agent_answer("当前授权上下文证据不足。")

    assert output.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert output.claims == []
    assert evidence_ids == ()


def test_parse_agent_answer_allows_empty_lines_between_bounded_claims() -> None:
    output, _ = parse_agent_answer(
        "第一条 [evidence:e-1]\n\n   \n第二条 [evidence:e-2]"
    )

    assert [claim.text for claim in output.claims] == ["第一条", "第二条"]


@pytest.mark.parametrize(
    "content",
    [
        "x" * 12_001 + " [evidence:e-1]",
        "\n".join(f"claim-{index} [evidence:e-{index}]" for index in range(33)),
        "x" * 2_001 + " [evidence:e-1]",
        "claim [evidence:" + ",".join(f"e-{index}" for index in range(11)) + "]",
        "claim [evidence:" + "e" * 256 + "]",
    ],
)
def test_parse_agent_answer_rejects_oversized_content_claims_or_markers(
    content: str,
) -> None:
    with pytest.raises(AgentAnswerContractError):
        parse_agent_answer(content)
