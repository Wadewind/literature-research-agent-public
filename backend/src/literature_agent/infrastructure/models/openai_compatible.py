"""基于 httpx2 的 OpenAI-compatible 模型 Adapter（切片 3）。

实现 ``EmbeddingModel``/``ChatModel`` Port，适用于智谱、DeepSeek 等
OpenAI 兼容端点；base_url/api_key/model 全部来自 Settings，不绑定具体
Provider。

错误分类与重试约定（2026-08-20 定稿）：
- 429/5xx/网络超时最多重试 ``max_retries`` 次（默认 2），固定小退避；
- 401/403/400 等永久错误不重试；
- 响应 JSON 畸形或缺字段为 ``ModelResponseError``（永久），结构修复属上层；
- 日志与异常消息不记录完整 Prompt 或响应体。
"""

import asyncio
import json
import logging
from typing import Any

import httpx2

from literature_agent.application.ports.chat_model import ChatModel
from literature_agent.application.ports.embedding_model import EmbeddingModel
from literature_agent.domain.model_errors import (
    ModelAuthError,
    ModelError,
    ModelInvalidRequestError,
    ModelRateLimitError,
    ModelResponseError,
    ModelServerError,
    ModelTimeoutError,
)
from literature_agent.domain.model_types import (
    ChatFinishReason,
    ChatMessage,
    ChatResult,
    EmbeddingResult,
    ModelUsage,
)

logger = logging.getLogger(__name__)

# 可重试的 HTTP 状态码：限流与服务端错误
_RETRYABLE_STATUS_CODES = frozenset({429}) | frozenset(range(500, 600))

_DEFAULT_BACKOFF_SECONDS = (1.0, 2.0)

_ERROR_MESSAGE_MAX_LENGTH = 200

_JSON_SCHEMA_INSTRUCTION_PREFIX = (
    "你必须输出结构化数据。只返回一个符合下方 JSON Schema 的 JSON object。"
    "不得返回 Markdown 代码块、解释文字或 Schema 未声明的额外字段。"
)


def _normalize_finish_reason(value: object) -> ChatFinishReason | None:
    """把 Provider 终止原因收敛到固定 allowlist，不透传未知字符串。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return ChatFinishReason.OTHER
    try:
        return ChatFinishReason(value)
    except ValueError:
        return ChatFinishReason.OTHER


class _OpenAiCompatibleBase:
    """共享的 HTTP 调用、重试与错误映射逻辑。"""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: tuple[float, ...] = _DEFAULT_BACKOFF_SECONDS,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._owns_client = client is None
        self._client = client or httpx2.AsyncClient(
            base_url=base_url,
            timeout=httpx2.Timeout(timeout_seconds),
        )

    async def aclose(self) -> None:
        """关闭自建 HTTP 客户端（外部注入的客户端由注入方管理）。"""
        if self._owns_client:
            await self._client.aclose()

    def _require_api_key(self) -> str:
        """返回 API Key；缺失时给出明确的永久错误。"""
        if not self._api_key:
            raise ModelAuthError(
                f"缺少 {self.provider} 的 API Key，请在 Settings 中配置"
            )
        return self._api_key

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx2.Response:
        """发送 POST 请求，按错误分类做有限短重试。"""
        headers = {"Authorization": f"Bearer {self._require_api_key()}"}
        attempt = 0
        while True:
            try:
                response = await self._client.post(path, json=payload, headers=headers)
            except httpx2.TimeoutException as exc:
                if attempt < self._max_retries:
                    await self._sleep_before_retry(attempt)
                    attempt += 1
                    continue
                raise ModelTimeoutError(
                    f"{self.provider} 请求超时（重试 {attempt} 次后仍失败）"
                ) from exc
            except httpx2.TransportError as exc:
                if attempt < self._max_retries:
                    await self._sleep_before_retry(attempt)
                    attempt += 1
                    continue
                raise ModelServerError(
                    f"{self.provider} 网络连接失败：{type(exc).__name__}"
                ) from exc
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                await self._sleep_before_retry(attempt)
                attempt += 1
                continue
            if response.status_code >= 400:
                raise _map_http_error(self.provider, response)
            return response

    async def _sleep_before_retry(self, attempt: int) -> None:
        """按固定小退避等待后重试；超出退避表时沿用最后一档。"""
        delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
        if delay > 0:
            await asyncio.sleep(delay)


def _extract_error_message(response: httpx2.Response) -> str:
    """从错误响应中提取截断后的安全描述（不含请求内容）。"""
    try:
        body = response.json()
        message = body.get("error", {}).get("message", "")
        if isinstance(message, str) and message:
            return message[:_ERROR_MESSAGE_MAX_LENGTH]
    except (ValueError, AttributeError):
        pass
    return f"HTTP {response.status_code}"


def _map_http_error(provider: str, response: httpx2.Response) -> ModelError:
    """把 HTTP 错误状态码映射为模型错误分类。"""
    status = response.status_code
    detail = _extract_error_message(response)
    if status in (401, 403):
        return ModelAuthError(f"{provider} 认证失败（HTTP {status}）：{detail}")
    if status == 429:
        return ModelRateLimitError(f"{provider} 限流（重试耗尽）：{detail}")
    if status >= 500:
        return ModelServerError(f"{provider} 服务端错误（HTTP {status}）：{detail}")
    return ModelInvalidRequestError(f"{provider} 请求非法（HTTP {status}）：{detail}")


def _parse_json(provider: str, response: httpx2.Response) -> dict[str, Any]:
    """解析响应 JSON；畸形响应映射为永久的响应形状错误。"""
    try:
        body = response.json()
    except ValueError as exc:
        raise ModelResponseError(f"{provider} 响应不是合法 JSON") from exc
    if not isinstance(body, dict):
        raise ModelResponseError(f"{provider} 响应顶层不是 JSON 对象")
    return body


def _parse_usage(body: dict[str, Any]) -> ModelUsage:
    """解析 usage 字段；缺失或形状异常时容忍为 None。"""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return ModelUsage()
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    return ModelUsage(
        prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
        completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
    )


def _json_object_schema_instruction(json_schema: dict) -> str:
    """把结构化契约确定性编码为 ``json_object`` 模式的系统指令。"""
    serialized_schema = json.dumps(
        json_schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"{_JSON_SCHEMA_INSTRUCTION_PREFIX}\n"
        "<BEGIN_JSON_SCHEMA>\n"
        f"{serialized_schema}\n"
        "<END_JSON_SCHEMA>"
    )


class OpenAiCompatibleEmbedding(_OpenAiCompatibleBase, EmbeddingModel):
    """OpenAI 兼容 Embedding Adapter（默认智谱 embedding-3 端点）。"""

    def __init__(self, *, dimensions: int | None = None, **kwargs: Any) -> None:
        """初始化 Embedding Adapter。

        参数:
            dimensions: 输出向量维度（如智谱 embedding-3 的 256/512/1024/2048）；
                None 表示不指定，由 Provider 默认。维度参与 embedding profile hash。
            **kwargs: 见 ``_OpenAiCompatibleBase``。
        """
        super().__init__(**kwargs)
        self._dimensions = dimensions

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """批量生成向量；空列表直接返回空结果，不发起请求。"""
        if not texts:
            return EmbeddingResult(
                vectors=[], model=self.model, usage=ModelUsage(prompt_tokens=0)
            )
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        if self._dimensions is not None:
            payload["dimensions"] = self._dimensions
        response = await self._post("/embeddings", payload)
        body = _parse_json(self.provider, response)
        data = body.get("data")
        if not isinstance(data, list):
            raise ModelResponseError(f"{self.provider} 响应缺少 data 字段")
        vectors: list[list[float]] = []
        try:
            for item in sorted(data, key=lambda item: item["index"]):
                vectors.append([float(value) for value in item["embedding"]])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelResponseError(
                f"{self.provider} 响应 data 字段形状非法"
            ) from exc
        if len(vectors) != len(texts):
            raise ModelResponseError(
                f"{self.provider} 返回向量数 {len(vectors)} 与输入数 {len(texts)} 不一致"
            )
        model = body.get("model")
        return EmbeddingResult(
            vectors=vectors,
            model=model if isinstance(model, str) else self.model,
            usage=_parse_usage(body),
        )


class OpenAiCompatibleChat(_OpenAiCompatibleBase, ChatModel):
    """OpenAI 兼容 ChatCompletions Adapter（默认 DeepSeek 端点）。"""

    def __init__(self, *, json_schema_supported: bool = True, **kwargs: Any) -> None:
        """初始化 Chat Adapter。

        参数:
            json_schema_supported: Provider 是否支持 ``json_schema`` 形态的
                ``response_format``；不支持时降级为 ``json_object``。
            **kwargs: 见 ``_OpenAiCompatibleBase``。
        """
        super().__init__(**kwargs)
        self._json_schema_supported = json_schema_supported

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """生成回复；结构化意图经 OpenAI ``response_format`` 表达。"""
        request_messages = [{"role": m.role, "content": m.content} for m in messages]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": request_messages,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_schema is not None:
            if self._json_schema_supported:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": json_schema},
                }
            else:
                payload["response_format"] = {"type": "json_object"}
                payload["messages"] = [
                    {
                        "role": "system",
                        "content": _json_object_schema_instruction(json_schema),
                    },
                    *request_messages,
                ]
        response = await self._post("/chat/completions", payload)
        body = _parse_json(self.provider, response)
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelResponseError(f"{self.provider} 响应缺少 choices 字段")
        choice = choices[0]
        try:
            content = choice["message"]["content"]
        except (KeyError, TypeError, IndexError) as exc:
            raise ModelResponseError(
                f"{self.provider} 响应 choices 字段形状非法"
            ) from exc
        if not isinstance(content, str):
            raise ModelResponseError(f"{self.provider} 响应 content 不是字符串")
        model = body.get("model")
        return ChatResult(
            content=content,
            model=model if isinstance(model, str) else self.model,
            usage=_parse_usage(body),
            finish_reason=_normalize_finish_reason(
                choice.get("finish_reason") if isinstance(choice, dict) else None
            ),
        )
