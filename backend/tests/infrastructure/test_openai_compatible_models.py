"""OpenAI-compatible Embedding/Chat Adapter 的 HTTP 契约测试。

使用 pytest-httpx2（RESPX）mock 传输层，不访问真实 Provider。
"""

import json

import httpx
import httpx2
import pytest
import respx

from literature_agent.domain.model_errors import (
    ModelAuthError,
    ModelInvalidRequestError,
    ModelRateLimitError,
    ModelResponseError,
    ModelServerError,
    ModelTimeoutError,
)
from literature_agent.domain.model_types import ChatFinishReason, ChatMessage
from literature_agent.domain.review_search_strategy import SEARCH_STRATEGY_JSON_SCHEMA
from literature_agent.infrastructure.models.openai_compatible import (
    OpenAiCompatibleChat,
    OpenAiCompatibleEmbedding,
)

_EMBEDDING_BASE_URL = "https://embedding.example.com/api/paas/v4"
_CHAT_BASE_URL = "https://chat.example.com"
# RESPX 路由器未设 base_url 时只匹配完整 URL 模式
_EMBEDDING_URL = f"{_EMBEDDING_BASE_URL}/embeddings"
_CHAT_URL = f"{_CHAT_BASE_URL}/chat/completions"
_NO_BACKOFF = (0.0, 0.0, 0.0)  # 测试中不退避等待


def _embedding_adapter(**overrides) -> OpenAiCompatibleEmbedding:
    """构造使用 mock 客户端的 Embedding Adapter。"""
    params = {
        "provider": "zhipu",
        "base_url": _EMBEDDING_BASE_URL,
        "api_key": "test-key",
        "model": "embedding-3",
        "dimensions": 1024,
        "max_retries": 2,
        "backoff_seconds": _NO_BACKOFF,
        "client": httpx2.AsyncClient(base_url=_EMBEDDING_BASE_URL, trust_env=False),
    }
    params.update(overrides)
    return OpenAiCompatibleEmbedding(**params)


def _chat_adapter(**overrides) -> OpenAiCompatibleChat:
    """构造使用 mock 客户端的 Chat Adapter。"""
    params = {
        "provider": "deepseek",
        "base_url": _CHAT_BASE_URL,
        "api_key": "test-key",
        "model": "deepseek-v4-flash",
        "max_retries": 2,
        "backoff_seconds": _NO_BACKOFF,
        "client": httpx2.AsyncClient(base_url=_CHAT_BASE_URL, trust_env=False),
    }
    params.update(overrides)
    return OpenAiCompatibleChat(**params)


def _embedding_payload(count: int = 2) -> dict:
    """构造合法的 Embedding 响应体。"""
    return {
        "model": "embedding-3",
        "data": [
            {"index": i, "embedding": [0.1 * (i + 1), 0.2]} for i in range(count)
        ],
        "usage": {"prompt_tokens": 11, "total_tokens": 11},
    }


def _chat_payload(
    content: str = '{"answer_status": "answered"}',
    *,
    finish_reason: str = "stop",
) -> dict:
    """构造合法的 Chat 响应体。"""
    return {
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 13, "total_tokens": 20},
    }


async def test_embedding_success(httpx2_mock: respx.Router) -> None:
    """Embedding 成功：向量、usage、模型名与请求形状正确。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        return_value=httpx.Response(200, json=_embedding_payload())
    )
    adapter = _embedding_adapter()

    result = await adapter.embed(["第一段", "第二段"])

    assert result.vectors == [[0.1, 0.2], [0.2, 0.2]]
    assert result.model == "embedding-3"
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens is None
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body == {"model": "embedding-3", "input": ["第一段", "第二段"], "dimensions": 1024}


async def test_embedding_empty_batch_no_request(httpx2_mock: respx.Router) -> None:
    """空批量直接返回空结果，不发起请求。

    不注册任何路由：若 Adapter 发出请求，RESPX 会直接断言失败。
    """
    adapter = _embedding_adapter()

    result = await adapter.embed([])

    assert result.vectors == []
    assert len(httpx2_mock.calls) == 0


async def test_embedding_missing_api_key(httpx2_mock: respx.Router) -> None:
    """缺少 API Key 时给出明确永久错误，不发起请求。"""
    adapter = _embedding_adapter(api_key=None)

    with pytest.raises(ModelAuthError):
        await adapter.embed(["文本"])
    assert len(httpx2_mock.calls) == 0


async def test_embedding_429_retry_then_success(httpx2_mock: respx.Router) -> None:
    """429 限流短重试后成功。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "rate limited"}}),
            httpx.Response(200, json=_embedding_payload(1)),
        ]
    )
    adapter = _embedding_adapter()

    result = await adapter.embed(["文本"])

    assert result.vectors == [[0.1, 0.2]]
    assert route.call_count == 2


async def test_embedding_429_exhausted(httpx2_mock: respx.Router) -> None:
    """429 重试耗尽（max_retries=2，共 3 次请求）后抛出限流错误。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    adapter = _embedding_adapter()

    with pytest.raises(ModelRateLimitError):
        await adapter.embed(["文本"])
    assert route.call_count == 3


async def test_embedding_5xx_exhausted(httpx2_mock: respx.Router) -> None:
    """5xx 重试耗尽后抛出服务端错误。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        return_value=httpx.Response(500, json={"error": {"message": "internal"}})
    )
    adapter = _embedding_adapter()

    with pytest.raises(ModelServerError):
        await adapter.embed(["文本"])
    assert route.call_count == 3


async def test_embedding_timeout_exhausted(httpx2_mock: respx.Router) -> None:
    """网络超时重试耗尽后抛出超时错误。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        side_effect=httpx2.ReadTimeout("timeout")
    )
    adapter = _embedding_adapter()

    with pytest.raises(ModelTimeoutError):
        await adapter.embed(["文本"])
    assert route.call_count == 3


async def test_embedding_401_no_retry(httpx2_mock: respx.Router) -> None:
    """401 认证失败属于永久错误，不重试。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid key"}})
    )
    adapter = _embedding_adapter()

    with pytest.raises(ModelAuthError):
        await adapter.embed(["文本"])
    assert route.call_count == 1


async def test_embedding_400_no_retry(httpx2_mock: respx.Router) -> None:
    """400 非法请求属于永久错误，不重试。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
    )
    adapter = _embedding_adapter()

    with pytest.raises(ModelInvalidRequestError):
        await adapter.embed(["文本"])
    assert route.call_count == 1


async def test_embedding_malformed_json(httpx2_mock: respx.Router) -> None:
    """响应 JSON 畸形抛出响应形状错误，不重试。"""
    route = httpx2_mock.post(_EMBEDDING_URL).mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    adapter = _embedding_adapter()

    with pytest.raises(ModelResponseError):
        await adapter.embed(["文本"])
    assert route.call_count == 1


async def test_embedding_missing_data_field(httpx2_mock: respx.Router) -> None:
    """响应缺少 data 字段抛出响应形状错误。"""
    httpx2_mock.post(_EMBEDDING_URL).mock(
        return_value=httpx.Response(200, json={"model": "embedding-3"})
    )
    adapter = _embedding_adapter()

    with pytest.raises(ModelResponseError):
        await adapter.embed(["文本"])


async def test_chat_success_with_json_schema(httpx2_mock: respx.Router) -> None:
    """Chat 成功：content、usage 与 response_format 请求形状正确。"""
    route = httpx2_mock.post(_CHAT_URL).mock(return_value=httpx.Response(200, json=_chat_payload()))
    adapter = _chat_adapter()
    schema = {"type": "object", "properties": {"answer_status": {"type": "string"}}}

    result = await adapter.generate(
        [ChatMessage(role="user", content="问题")],
        json_schema=schema,
        max_tokens=512,
    )

    assert result.content == '{"answer_status": "answered"}'
    assert result.model == "deepseek-v4-flash"
    assert result.usage.prompt_tokens == 7
    assert result.usage.completion_tokens == 13
    assert result.finish_reason is ChatFinishReason.STOP
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "deepseek-v4-flash"
    assert body["messages"] == [{"role": "user", "content": "问题"}]
    assert body["max_tokens"] == 512
    assert "thinking" not in body
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": schema},
    }


async def test_chat_explicitly_disables_thinking_for_registered_provider(
    httpx2_mock: respx.Router,
) -> None:
    """已注册 Provider 可以固定关闭 thinking，避免推理耗尽结构化输出预算。"""
    route = httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(200, json=_chat_payload())
    )
    adapter = _chat_adapter(thinking_mode="disabled")

    await adapter.generate([ChatMessage(role="user", content="问题")], max_tokens=512)

    body = json.loads(route.calls.last.request.content)
    assert body["thinking"] == {"type": "disabled"}


def test_chat_rejects_unregistered_thinking_mode() -> None:
    """通用 Adapter 不接受调用方借 thinking 参数扩大 Provider 行为。"""
    with pytest.raises(ValueError, match="thinking_mode"):
        _chat_adapter(thinking_mode="auto")


async def test_chat_enables_registered_thinking_with_bounded_effort(
    httpx2_mock: respx.Router,
) -> None:
    """开发诊断可显式开启 thinking，并发送受限 reasoning effort。"""
    route = httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(200, json=_chat_payload())
    )
    adapter = _chat_adapter(thinking_mode="enabled", reasoning_effort="low")

    await adapter.generate([ChatMessage(role="user", content="问题")], max_tokens=512)

    body = json.loads(route.calls.last.request.content)
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"


@pytest.mark.parametrize("reasoning_effort", ["medium", "xhigh"])
def test_chat_rejects_unregistered_reasoning_effort(reasoning_effort: str) -> None:
    with pytest.raises(ValueError, match="reasoning_effort"):
        _chat_adapter(thinking_mode="enabled", reasoning_effort=reasoning_effort)


def test_chat_rejects_reasoning_effort_when_thinking_is_disabled() -> None:
    with pytest.raises(ValueError, match="仅可用于"):
        _chat_adapter(thinking_mode="disabled", reasoning_effort="low")


async def test_chat_unknown_finish_reason_is_safely_normalized(
    httpx2_mock: respx.Router,
) -> None:
    """Provider 新增未知终止原因时只保存 allowlist 内的 ``other``。"""
    httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json=_chat_payload(finish_reason="provider-private-reason"),
        )
    )

    result = await _chat_adapter().generate([ChatMessage(role="user", content="问题")])

    assert result.finish_reason is ChatFinishReason.OTHER


async def test_chat_length_finish_reason_is_preserved_with_empty_content(
    httpx2_mock: respx.Router,
) -> None:
    """Provider 触顶且未产生可见正文时必须保留 ``length`` 供业务层分类。"""
    httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json=_chat_payload(content="", finish_reason="length"),
        )
    )

    result = await _chat_adapter().generate([ChatMessage(role="user", content="问题")])

    assert result.content == ""
    assert result.finish_reason is ChatFinishReason.LENGTH


async def test_chat_json_object_fallback(httpx2_mock: respx.Router) -> None:
    """json_object 降级把完整 Schema 确定性注入请求且不修改调用方消息。"""
    route = httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(200, json=_chat_payload())
    )
    adapter = _chat_adapter(json_schema_supported=False)
    messages = [
        ChatMessage(role="system", content="原始系统约束"),
        ChatMessage(role="user", content="问题"),
    ]
    original_messages = list(messages)
    original_message_ids = [id(message) for message in messages]

    await adapter.generate(messages, json_schema=SEARCH_STRATEGY_JSON_SCHEMA)
    await adapter.generate(messages, json_schema=SEARCH_STRATEGY_JSON_SCHEMA)

    first_body = json.loads(route.calls[0].request.content)
    second_body = json.loads(route.calls[1].request.content)
    assert first_body["response_format"] == {"type": "json_object"}
    assert first_body["messages"][1:] == [
        {"role": "system", "content": "原始系统约束"},
        {"role": "user", "content": "问题"},
    ]
    schema_instruction = first_body["messages"][0]
    assert schema_instruction["role"] == "system"
    assert json.dumps(
        SEARCH_STRATEGY_JSON_SCHEMA,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) in schema_instruction["content"]
    assert "只返回一个" in schema_instruction["content"]
    assert "JSON object" in schema_instruction["content"]
    assert "Markdown" in schema_instruction["content"]
    assert "解释" in schema_instruction["content"]
    assert "额外字段" in schema_instruction["content"]
    assert first_body["messages"][0] == second_body["messages"][0]
    assert messages == original_messages
    assert [id(message) for message in messages] == original_message_ids


async def test_chat_plain_text_no_response_format(httpx2_mock: respx.Router) -> None:
    """不传 json_schema 时即便关闭 Schema 能力也不注入或发送 response_format。"""
    route = httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(200, json=_chat_payload("自由文本"))
    )
    adapter = _chat_adapter(json_schema_supported=False)
    messages = [ChatMessage(role="user", content="问题")]

    result = await adapter.generate(messages)

    assert result.content == "自由文本"
    body = json.loads(route.calls.last.request.content)
    assert "response_format" not in body
    assert body["messages"] == [{"role": "user", "content": "问题"}]


async def test_chat_401_no_retry(httpx2_mock: respx.Router) -> None:
    """Chat 403 认证失败不重试。"""
    route = httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(403, json={"error": {"message": "forbidden"}})
    )
    adapter = _chat_adapter()

    with pytest.raises(ModelAuthError):
        await adapter.generate([ChatMessage(role="user", content="问题")])
    assert route.call_count == 1


async def test_chat_missing_choices(httpx2_mock: respx.Router) -> None:
    """Chat 响应缺少 choices 字段抛出响应形状错误。"""
    httpx2_mock.post(_CHAT_URL).mock(
        return_value=httpx.Response(200, json={"model": "deepseek-v4-flash"})
    )
    adapter = _chat_adapter()

    with pytest.raises(ModelResponseError):
        await adapter.generate([ChatMessage(role="user", content="问题")])
