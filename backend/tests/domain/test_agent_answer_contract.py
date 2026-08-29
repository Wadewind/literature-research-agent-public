"""Research Agent 行内 Evidence 标记契约。"""

import pytest

from literature_agent.domain.agent_answer import (
    AgentAnswerContractError,
    canonicalize_agent_answer,
    extract_agent_evidence_claims,
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


def test_extract_agent_evidence_claims_ignores_unmarked_world_knowledge_and_receipts() -> None:
    content = (
        "## 综合结果\n"
        "该方向由项目论文提出 [evidence:e-1]\n"
        "另外检索到 https://arxiv.org/abs/2401.00001 。\n"
        "已生成 hallucination.txt，可在成果区下载。"
    )

    output, evidence_ids = extract_agent_evidence_claims(content)

    assert [claim.text for claim in output.claims] == ["该方向由项目论文提出"]
    assert output.claims[0].evidence_ids == ["e-1"]
    assert evidence_ids == ("e-1",)


@pytest.mark.parametrize(
    "content",
    [
        "普通说明\n论述 [evidence:]",
        "论述 [evidence:e-1] 后面还有内容\n普通说明",
        "合法论述 [evidence:e-1]\n伪造论述 [evidence:e-2,e-2]",
    ],
)
def test_extract_agent_evidence_claims_rejects_any_malformed_explicit_marker(
    content: str,
) -> None:
    with pytest.raises(AgentAnswerContractError):
        extract_agent_evidence_claims(content)


def test_canonicalize_agent_answer_keeps_only_valid_grounded_markdown_claims() -> None:
    content = (
        "下面给出综合比较：\n"
        "## 方法差异\n"
        "- 检索增强方法依赖外部知识[evidence:e-1,e-2]\n"
        "- 参数方法修改模型内部表示 [evidence:e-3]\n"
        "## 尚未解决的问题\n"
        "- 缺少直接证据[evidence:暂无直接证据]\n"
        "这些结论仍需更多研究。"
    )

    canonical = canonicalize_agent_answer(content)
    output, evidence_ids = parse_agent_answer(canonical)

    assert canonical == (
        "检索增强方法依赖外部知识 [evidence:e-1,e-2]\n"
        "参数方法修改模型内部表示 [evidence:e-3]"
    )
    assert [claim.text for claim in output.claims] == [
        "检索增强方法依赖外部知识",
        "参数方法修改模型内部表示",
    ]
    assert evidence_ids == ("e-1", "e-2", "e-3")


def test_canonicalize_agent_answer_rejects_when_no_valid_grounded_claim_remains() -> None:
    with pytest.raises(AgentAnswerContractError):
        canonicalize_agent_answer("## 结论\n没有引用的论述")


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
