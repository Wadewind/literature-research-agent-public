"""RAG 结构化输出 Schema 测试（切片 7）。

覆盖 ``RagAnswerOutput`` 的解析成功/失败路径：合法输出、缺字段、
非法 answer_status、claims 类型错误、额外字段拒绝、非 JSON 内容，
以及供 ChatModel 使用的 JSON Schema 形状。
"""

import json

import pytest

from literature_agent.domain.answer_schema import (
    RagAnswerOutput,
    parse_rag_answer_output,
    rag_answer_json_schema,
)
from literature_agent.domain.evidence import AnswerStatus
from literature_agent.domain.exceptions import AnswerOutputParseError


def test_parse_valid_answered_output() -> None:
    """合法 answered 输出解析成功，字段原样保留。"""
    content = json.dumps(
        {
            "answer_status": "answered",
            "claims": [
                {"text": "GNN 适合分子性质预测", "evidence_ids": ["e1", "e2"]},
                {"text": "RL 已用于机器人控制", "evidence_ids": ["e3"]},
            ],
        }
    )

    output = parse_rag_answer_output(content)

    assert output.answer_status is AnswerStatus.ANSWERED
    assert len(output.claims) == 2
    assert output.claims[0].text == "GNN 适合分子性质预测"
    assert output.claims[0].evidence_ids == ["e1", "e2"]


def test_parse_valid_insufficient_evidence_output() -> None:
    """合法 insufficient_evidence 输出（claims 为空）解析成功。"""
    output = parse_rag_answer_output(
        json.dumps({"answer_status": "insufficient_evidence", "claims": []})
    )

    assert output.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert output.claims == []


def test_parse_missing_field_rejected() -> None:
    """缺少 answer_status 字段时报解析错误。"""
    with pytest.raises(AnswerOutputParseError):
        parse_rag_answer_output(json.dumps({"claims": []}))


def test_parse_invalid_status_rejected() -> None:
    """answer_status 取值非法时报解析错误。"""
    with pytest.raises(AnswerOutputParseError):
        parse_rag_answer_output(
            json.dumps({"answer_status": "partial", "claims": []})
        )


def test_parse_claims_wrong_type_rejected() -> None:
    """claims 不是列表或 ClaimDraft 缺字段时报解析错误。"""
    with pytest.raises(AnswerOutputParseError):
        parse_rag_answer_output(
            json.dumps({"answer_status": "answered", "claims": "not-a-list"})
        )
    with pytest.raises(AnswerOutputParseError):
        parse_rag_answer_output(
            json.dumps({"answer_status": "answered", "claims": [{"text": "x"}]})
        )
    with pytest.raises(AnswerOutputParseError):
        parse_rag_answer_output(
            json.dumps(
                {
                    "answer_status": "answered",
                    "claims": [{"text": "x", "evidence_ids": "e1"}],
                }
            )
        )


def test_parse_extra_field_rejected() -> None:
    """携带未定义字段时报解析错误（Schema 对模型输出保持严格）。"""
    with pytest.raises(AnswerOutputParseError):
        parse_rag_answer_output(
            json.dumps({"answer_status": "answered", "claims": [], "extra": 1})
        )


def test_parse_non_json_rejected() -> None:
    """非 JSON 内容报解析错误。"""
    with pytest.raises(AnswerOutputParseError):
        parse_rag_answer_output("这不是 JSON")


def test_json_schema_shape() -> None:
    """生成的 JSON Schema 含两个状态枚举与 ClaimDraft 定义，可直接传给模型。"""
    schema = rag_answer_json_schema()

    assert set(schema["$defs"]["AnswerStatus"]["enum"]) == {
        "answered",
        "insufficient_evidence",
    }
    assert schema["additionalProperties"] is False
    claim_draft = schema["$defs"]["ClaimDraft"]
    assert claim_draft["additionalProperties"] is False
    assert set(claim_draft["properties"]) == {"text", "evidence_ids"}
    # Schema 是稳定字典，两次生成一致
    assert schema == RagAnswerOutput.model_json_schema()
