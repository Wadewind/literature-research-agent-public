"""模型调用的值对象（结果与用量）。

只承载结构化结果与 token 用量，不含完整 Prompt 或响应原文之外的
Provider 原始负载；调用内容本身也不进入日志与 Event。
"""

from dataclasses import dataclass
from enum import StrEnum


class ChatFinishReason(StrEnum):
    """Chat 调用的安全终止原因；未知 Provider 值统一归为 ``other``。"""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"
    FUNCTION_CALL = "function_call"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条对话消息。

    属性:
        role: 消息角色（system/user/assistant）。
        content: 消息文本内容。
    """

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """一次模型调用的 token 用量；Provider 未返回的字段为 None。"""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Embedding 批量调用结果。

    属性:
        vectors: 与输入文本一一对应的向量列表。
        model: Provider 实际使用的模型名。
        usage: token 用量。
    """

    vectors: list[list[float]]
    model: str
    usage: ModelUsage


@dataclass(frozen=True, slots=True)
class ChatResult:
    """Chat 生成结果。

    属性:
        content: 原始 content 字符串；JSON 解析与业务 Schema 校验留给上层。
        model: Provider 实际使用的模型名。
        usage: token 用量。
        finish_reason: allowlist 化的 Provider 终止原因；未知或未返回为 None。
    """

    content: str
    model: str
    usage: ModelUsage
    finish_reason: ChatFinishReason | None = None
