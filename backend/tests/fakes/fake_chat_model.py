"""ChatModel 的脚本化假实现。

响应来自构造时给定的队列（字符串或异常），队列耗尽后返回固定默认
JSON，供不访问真实 Provider 的测试与本地开发使用。
"""

from literature_agent.application.ports.chat_model import ChatModel
from literature_agent.domain.model_types import ChatMessage, ChatResult, ModelUsage

_DEFAULT_RESPONSE = '{"answer_status": "insufficient_evidence", "claims": []}'


class FakeChatModel(ChatModel):
    """不依赖外部服务的 Chat 假实现。"""

    provider = "fake"

    def __init__(
        self,
        responses: list[str | Exception] | None = None,
        default_response: str = _DEFAULT_RESPONSE,
    ) -> None:
        """初始化 Fake Chat。

        参数:
            responses: 脚本化响应队列，按调用顺序消费；元素为 Exception
                时抛出（测试失败路径用）。
            default_response: 队列耗尽后的固定响应。
        """
        self.model = "fake-chat"
        self._responses = list(responses) if responses else []
        self._default_response = default_response
        self.calls: list[list[ChatMessage]] = []

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """记录调用并返回脚本化响应。"""
        self.calls.append(list(messages))
        queued: str | Exception = (
            self._responses.pop(0) if self._responses else self._default_response
        )
        if isinstance(queued, Exception):
            raise queued
        return ChatResult(
            content=queued,
            model=self.model,
            usage=ModelUsage(prompt_tokens=10, completion_tokens=5),
        )
