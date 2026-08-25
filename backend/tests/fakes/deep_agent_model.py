"""Deep Agents Adapter 测试使用的完全离线脚本模型。"""

import asyncio
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from pydantic import Field, PrivateAttr


class ScriptedDeepAgentChatModel(BaseChatModel):
    """区分普通调用与摘要调用，并记录模型实际看到的工具和消息。"""

    model_name: str = "phase5-fake-chat"
    model_call_count: int = 0
    summary_call_count: int = 0
    visible_tool_names: list[tuple[str, ...]] = Field(default_factory=list)
    observed_message_text: list[tuple[str, ...]] = Field(default_factory=list)
    _tool_requested: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self) -> str:
        return "phase5-deep-agent-fake"

    def _get_ls_params(self, **kwargs: Any) -> dict[str, Any]:
        return {
            **super()._get_ls_params(**kwargs),
            "ls_provider": "phase5fake",
            "ls_model_name": self.model_name,
        }

    def bind_tools(
        self,
        tools: list[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        del tool_choice, kwargs
        self.visible_tool_names.append(tuple(tool.name for tool in tools))
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._next_message(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # LangGraph/LangChain 的异步中间件链需要一次真实调度点；不访问外部资源。
        await asyncio.sleep(0.001)
        return self._generate(messages, stop, run_manager, **kwargs)

    def _next_message(self, messages: list[BaseMessage]) -> AIMessage:
        text = tuple(message.text for message in messages)
        self.observed_message_text.append(text)
        if any("Context Extraction Assistant" in item for item in text):
            self.summary_call_count += 1
            return AIMessage(content="已压缩：第一轮完成了受控研究记录。")

        self.model_call_count += 1
        if any(isinstance(message, ToolMessage) for message in messages[-2:]):
            return AIMessage(content="第一轮分析完成。")
        if not self._tool_requested and any("第一轮" in item for item in text):
            self._tool_requested = True
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "record_research_step",
                        "args": {"note": "第一轮受控记录"},
                        "id": "record-call-1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="第二轮基于同一 Thread 的压缩上下文继续完成。")
