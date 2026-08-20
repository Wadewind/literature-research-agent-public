"""真实 Provider 冒烟测试（默认跳过）。

各调用一次真实 API 断言响应形状，Key 从环境变量读取。
显式启用：``AGENT_RUN_PROVIDER_TESTS=1 uv run pytest tests/infrastructure/test_provider_smoke.py``。

注意：Adapter 使用 httpx2 默认的 trust_env 行为，当前 shell 的 http_proxy/
all_proxy 等环境变量会生效；若 all_proxy 为 socks 协议需额外安装 socksio。
"""

import os

import pytest

from literature_agent.domain.model_types import ChatMessage
from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.models.openai_compatible import (
    OpenAiCompatibleChat,
    OpenAiCompatibleEmbedding,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_PROVIDER_TESTS") != "1",
    reason="真实 Provider 冒烟测试需显式启用（AGENT_RUN_PROVIDER_TESTS=1）",
)


async def test_real_embedding_smoke() -> None:
    """真实 Embedding 调用：返回与维度配置一致的向量与 usage。"""
    settings = Settings.from_env()
    if not settings.embedding_api_key:
        pytest.skip("未配置 AGENT_EMBEDDING_API_KEY")
    adapter = OpenAiCompatibleEmbedding(
        provider="zhipu",
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
    )
    try:
        result = await adapter.embed(["文献综述 Agent 冒烟测试"])
    finally:
        await adapter.aclose()

    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == settings.embedding_dimensions
    assert result.usage.prompt_tokens is not None and result.usage.prompt_tokens > 0


async def test_real_chat_smoke() -> None:
    """真实 Chat 调用：返回非空 content 与 usage。"""
    settings = Settings.from_env()
    if not settings.chat_api_key:
        pytest.skip("未配置 AGENT_CHAT_API_KEY")
    adapter = OpenAiCompatibleChat(
        provider="deepseek",
        base_url=settings.chat_base_url,
        api_key=settings.chat_api_key,
        model=settings.chat_model,
        timeout_seconds=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
    )
    try:
        result = await adapter.generate(
            [ChatMessage(role="user", content="用一句话回答：什么是文献综述？")],
            max_tokens=100,
        )
    finally:
        await adapter.aclose()

    assert result.content.strip()
    assert result.usage.prompt_tokens is not None and result.usage.prompt_tokens > 0
    assert result.usage.completion_tokens is not None and result.usage.completion_tokens > 0
