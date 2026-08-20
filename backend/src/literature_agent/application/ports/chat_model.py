"""Chat 模型端口。"""

from typing import Protocol

from literature_agent.domain.model_types import ChatMessage, ChatResult


class ChatModel(Protocol):
    """Chat 模型的抽象端口。

    结构化输出只表达意图（``json_schema``），具体 ``response_format``
    形态由 Adapter 决定；JSON 解析与业务 Schema 校验留给上层。
    ``provider``/``model`` 属性用于调用记录（ModelInvocation）。
    """

    provider: str
    model: str

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """生成回复并返回 Usage。

        参数:
            messages: 对话消息列表。
            json_schema: 期望的 JSON Schema；None 表示自由文本。
            max_tokens: 输出 token 上限；None 表示不限制。

        返回:
            原始 content 字符串、模型名与 token 用量。

        异常:
            ModelError: 调用失败，按子类区分临时/永久错误。
        """
        ...
