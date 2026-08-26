"""Research Agent DeepSeek BaseChatModel factory 测试。"""

from unittest.mock import AsyncMock, Mock

import pytest
from langchain_deepseek import ChatDeepSeek
from pydantic import SecretStr

from literature_agent.infrastructure.agent.deepseek_research_model import (
    aclose_deepseek_research_model,
    build_deepseek_research_model,
)


def test_factory_builds_fixed_non_thinking_bounded_model(monkeypatch) -> None:
    """Factory 只传入已确认的 Provider 参数并固定关闭 thinking。"""
    captured: dict[str, object] = {}

    class StubChatDeepSeek:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "literature_agent.infrastructure.agent.deepseek_research_model.ChatDeepSeek",
        StubChatDeepSeek,
    )

    model = build_deepseek_research_model(
        base_url="https://agent.example/v1",
        api_key="provider-secret",
        model="deepseek-v4-flash",
        max_output_tokens=1536,
        timeout_seconds=12.5,
        max_retries=1,
    )

    assert model.__class__ is StubChatDeepSeek
    assert isinstance(captured["api_key"], SecretStr)
    assert captured["api_key"].get_secret_value() == "provider-secret"
    assert {key: value for key, value in captured.items() if key != "api_key"} == {
        "base_url": "https://agent.example/v1",
        "model": "deepseek-v4-flash",
        "max_tokens": 1536,
        "timeout": 12.5,
        "max_retries": 1,
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def test_factory_rejects_model_drift_and_does_not_echo_secret() -> None:
    """7.0 固定模型，错误不得包含 Secret。"""
    secret = "provider-secret-must-not-leak"

    try:
        build_deepseek_research_model(
            base_url="https://agent.example/v1",
            api_key=secret,
            model="another-model",
            max_output_tokens=1536,
            timeout_seconds=12.5,
            max_retries=1,
        )
    except ValueError as exc:
        assert "AGENT_RESEARCH_MODEL" in str(exc)
        assert secret not in str(exc)
    else:  # pragma: no cover - 必须拒绝模型漂移
        raise AssertionError("未拒绝非固定 Research Agent 模型")


async def test_model_close_releases_sync_and_async_clients() -> None:
    """Worker shutdown 通过 factory 伴随的清理函数释放 HTTP 客户端。"""
    async_client = AsyncMock()
    sync_client = Mock()
    model = Mock(root_async_client=async_client, root_client=sync_client)

    await aclose_deepseek_research_model(model)

    async_client.close.assert_awaited_once_with()
    sync_client.close.assert_called_once_with()


async def test_model_close_releases_sync_client_when_async_close_fails() -> None:
    """异步客户端关闭失败也不能跳过同步客户端清理。"""
    async_client = AsyncMock()
    async_client.close.side_effect = RuntimeError("async close failed")
    sync_client = Mock()
    model = Mock(root_async_client=async_client, root_client=sync_client)

    with pytest.raises(RuntimeError, match="async close failed"):
        await aclose_deepseek_research_model(model)

    async_client.close.assert_awaited_once_with()
    sync_client.close.assert_called_once_with()


async def test_factory_constructs_locked_chatdeepseek_without_network(monkeypatch) -> None:
    """锁定版本必须接受当前 factory 参数；构造和关闭均不发网络请求。"""
    for name in (
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)

    model = build_deepseek_research_model(
        base_url="https://api.deepseek.com",
        api_key="offline-dummy-key",
        model="deepseek-v4-flash",
        max_output_tokens=123,
        timeout_seconds=1,
        max_retries=0,
    )
    try:
        assert isinstance(model, ChatDeepSeek)
        assert model.model_name == "deepseek-v4-flash"
        assert model.max_tokens == 123
        assert model.extra_body == {"thinking": {"type": "disabled"}}
    finally:
        await aclose_deepseek_research_model(model)
