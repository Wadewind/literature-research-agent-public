"""RAG 结构化输出 Schema（切片 7 定稿）。

定义切片 8 传给 ``ChatModel`` 的 ``json_schema`` 以及模型返回内容的
解析校验模型。Schema 对模型输出保持严格（拒绝未定义字段），减少
幻觉字段进入校验链路的空间。

一致性规则（answered 时 claims 非空、insufficient_evidence 时
claims 必须为空、每个 Claim 至少绑定一个 Evidence 等）无法在
JSON Schema 中表达，由 ``domain.citation_validator`` 确定性校验。
"""

import json

from pydantic import BaseModel, ConfigDict

from literature_agent.domain.evidence import AnswerStatus
from literature_agent.domain.exceptions import AnswerOutputParseError


class ClaimDraft(BaseModel):
    """模型生成的一个段落级论述草稿。

    属性:
        text: 论述文本。
        evidence_ids: 支撑该论述的 Evidence ID 列表
            （answered 状态下必须非空，由 Validator 校验）。
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str]


class RagAnswerOutput(BaseModel):
    """RAG 回答的结构化输出。

    属性:
        answer_status: 回答状态（answered / insufficient_evidence）。
        claims: 段落级论述列表；``insufficient_evidence`` 时必须为空
            （由 Validator 校验）。
    """

    model_config = ConfigDict(extra="forbid")

    answer_status: AnswerStatus
    claims: list[ClaimDraft]


def rag_answer_json_schema() -> dict:
    """返回供 ``ChatModel.generate(json_schema=...)`` 使用的 JSON Schema。"""
    return RagAnswerOutput.model_json_schema()


def parse_rag_answer_output(content: str) -> RagAnswerOutput:
    """解析并校验模型返回的原始内容。

    参数:
        content: ChatModel 返回的原始字符串（应为 JSON）。

    返回:
        校验通过的 ``RagAnswerOutput``。

    异常:
        AnswerOutputParseError: 内容不是合法 JSON 或不符合 Schema
            （缺字段、非法状态、类型错误、多余字段等）。
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AnswerOutputParseError(f"模型输出不是合法 JSON: {exc.msg}") from exc
    try:
        return RagAnswerOutput.model_validate(data)
    except ValueError as exc:
        raise AnswerOutputParseError("模型输出不符合 RagAnswerOutput Schema") from exc
