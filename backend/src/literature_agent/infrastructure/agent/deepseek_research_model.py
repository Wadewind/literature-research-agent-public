"""Research Agent 专用 DeepSeek ``BaseChatModel`` factory。"""

from typing import cast

from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from pydantic import SecretStr

_FIXED_MODEL = "deepseek-v4-flash"
_FIXED_MAX_OUTPUT_TOKENS = 4_096
_THINKING_MODES = frozenset({"disabled", "enabled"})
_REASONING_EFFORTS = frozenset({"low", "high", "max"})


def build_deepseek_research_model(
    *,
    base_url: str,
    api_key: str,
    model: str,
    max_output_tokens: int,
    thinking_mode: str,
    reasoning_effort: str,
    timeout_seconds: float,
    max_retries: int,
) -> BaseChatModel:
    """构造 thinking 受控、具有输出与重试上限的 Agent 模型。"""
    if model != _FIXED_MODEL:
        raise ValueError("AGENT_RESEARCH_MODEL 当前只允许 deepseek-v4-flash")
    if not api_key:
        raise ValueError("deep_agents 模式必须设置 AGENT_RESEARCH_MODEL_API_KEY")
    if not 0 < max_output_tokens <= _FIXED_MAX_OUTPUT_TOKENS:
        raise ValueError("AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS 必须在 1..4096 范围内")
    if thinking_mode not in _THINKING_MODES:
        raise ValueError("AGENT_RESEARCH_MODEL_THINKING_MODE 未在平台注册")
    if reasoning_effort not in _REASONING_EFFORTS:
        raise ValueError("AGENT_RESEARCH_MODEL_REASONING_EFFORT 未在平台注册")
    if timeout_seconds <= 0:
        raise ValueError("AGENT_MODEL_TIMEOUT_SECONDS 必须为正数")
    if max_retries < 0:
        raise ValueError("AGENT_MODEL_MAX_RETRIES 不能为负数")
    return cast(
        BaseChatModel,
        ChatDeepSeek(
            base_url=base_url,
            api_key=SecretStr(api_key),
            model=model,
            max_tokens=max_output_tokens,
            timeout=timeout_seconds,
            max_retries=max_retries,
            extra_body={"thinking": {"type": thinking_mode}},
            reasoning_effort=reasoning_effort if thinking_mode == "enabled" else None,
        ),
    )


async def aclose_deepseek_research_model(model: BaseChatModel) -> None:
    """关闭 ChatDeepSeek 内部同步与异步 OpenAI 客户端。"""
    root_async_client = getattr(model, "root_async_client", None)
    try:
        if root_async_client is not None:
            await root_async_client.close()
    finally:
        root_client = getattr(model, "root_client", None)
        if root_client is not None:
            root_client.close()
