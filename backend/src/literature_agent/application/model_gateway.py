"""Model Gateway 应用服务（切片 3）。

包装 ``EmbeddingModel``/``ChatModel`` Port，统一计时并把每次调用记录
（ModelInvocation）经 Repository 持久化。记录使用独立短事务，持久化
失败只记日志，不影响调用结果本身。

本切片只交付 Gateway 与记录能力；执行器接线（传入 run_id）在切片 5/8。
"""

import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.ports.chat_model import ChatModel
from literature_agent.application.ports.embedding_model import EmbeddingModel
from literature_agent.application.ports.model_invocation_repository import (
    ModelInvocationRepository,
)
from literature_agent.application.ports.session import Session
from literature_agent.domain.model_invocation import (
    InvocationStatus,
    ModelCapability,
    create_model_invocation,
)
from literature_agent.domain.model_types import (
    ChatMessage,
    ChatResult,
    EmbeddingResult,
)
from literature_agent.observability import log_event

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)


class ModelGateway[TSession: Session]:
    """Embedding/Chat 模型调用的统一入口与调用记录。

    不变量:
        - 模型调用不发生在数据库事务内；
        - 每次调用（无论成败）产生一条 ModelInvocation 记录，
          记录只含用量/延迟/错误分类，不含 Prompt 或响应内容；
        - 记录持久化失败只记日志，不影响调用结果。
    """

    def __init__(
        self,
        *,
        embedding_model: EmbeddingModel,
        chat_model: ChatModel,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        invocation_repo_factory: Callable[[TSession], ModelInvocationRepository],
    ) -> None:
        """初始化 ModelGateway。

        参数:
            embedding_model: Embedding 模型 Port 实现。
            chat_model: Chat 模型 Port 实现。
            session_factory: 返回异步上下文管理器的工厂，用于独立短事务。
            invocation_repo_factory: 根据 session 创建 Repository 的工厂。
        """
        self._embedding_model = embedding_model
        self._chat_model = chat_model
        self._session_factory = session_factory
        self._invocation_repo_factory = invocation_repo_factory

    async def embed(self, texts: list[str], *, run_id: str | None = None) -> EmbeddingResult:
        """批量生成向量并记录调用。

        参数:
            texts: 输入文本列表；空列表直接返回空结果，不发起请求。
            run_id: 关联的 Run 标识符，执行器接线后传入。

        异常:
            ModelError: 模型调用失败（已记录为 failed 后原样抛出）。
        """
        started = time.monotonic()
        try:
            result = await self._embedding_model.embed(texts)
        except Exception as exc:
            latency_ms = self._elapsed_ms(started)
            await self._record(
                run_id=run_id,
                capability=ModelCapability.EMBEDDING,
                port=self._embedding_model,
                status=InvocationStatus.FAILED,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
            )
            self._log_request(
                capability=ModelCapability.EMBEDDING,
                status=InvocationStatus.FAILED,
                latency_ms=latency_ms,
                run_id=run_id,
                error_code=type(exc).__name__,
            )
            raise
        latency_ms = self._elapsed_ms(started)
        await self._record(
            run_id=run_id,
            capability=ModelCapability.EMBEDDING,
            port=self._embedding_model,
            status=InvocationStatus.SUCCEEDED,
            latency_ms=latency_ms,
            model=result.model,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
        )
        self._log_request(
            capability=ModelCapability.EMBEDDING,
            status=InvocationStatus.SUCCEEDED,
            latency_ms=latency_ms,
            run_id=run_id,
            model=result.model,
        )
        return result

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
    ) -> ChatResult:
        """生成回复并记录调用。

        参数:
            messages: 对话消息列表。
            json_schema: 期望的 JSON Schema（只表达意图，校验在上层）。
            max_tokens: 输出 token 上限。
            run_id: 关联的 Run 标识符，执行器接线后传入。

        异常:
            ModelError: 模型调用失败（已记录为 failed 后原样抛出）。
        """
        started = time.monotonic()
        try:
            result = await self._chat_model.generate(
                messages, json_schema=json_schema, max_tokens=max_tokens
            )
        except Exception as exc:
            latency_ms = self._elapsed_ms(started)
            await self._record(
                run_id=run_id,
                capability=ModelCapability.CHAT,
                port=self._chat_model,
                status=InvocationStatus.FAILED,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
            )
            self._log_request(
                capability=ModelCapability.CHAT,
                status=InvocationStatus.FAILED,
                latency_ms=latency_ms,
                run_id=run_id,
                error_code=type(exc).__name__,
            )
            raise
        latency_ms = self._elapsed_ms(started)
        await self._record(
            run_id=run_id,
            capability=ModelCapability.CHAT,
            port=self._chat_model,
            status=InvocationStatus.SUCCEEDED,
            latency_ms=latency_ms,
            model=result.model,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
        )
        self._log_request(
            capability=ModelCapability.CHAT,
            status=InvocationStatus.SUCCEEDED,
            latency_ms=latency_ms,
            run_id=run_id,
            model=result.model,
        )
        return result

    def _log_request(
        self,
        *,
        capability: ModelCapability,
        status: InvocationStatus,
        latency_ms: int,
        run_id: str | None,
        error_code: str | None = None,
        model: str | None = None,
    ) -> None:
        port = (
            self._embedding_model if capability == ModelCapability.EMBEDDING else self._chat_model
        )
        log_event(
            logger,
            logging.INFO if status == InvocationStatus.SUCCEEDED else logging.WARNING,
            "model_request_completed"
            if status == InvocationStatus.SUCCEEDED
            else "model_request_failed",
            operation=capability.value,
            provider=port.provider,
            model=model or port.model,
            status=status.value,
            duration_ms=latency_ms,
            run_id=run_id,
            error_code=error_code,
            exception_type=error_code,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        """计算自 started 起的毫秒数。"""
        return int((time.monotonic() - started) * 1000)

    async def _record(
        self,
        *,
        run_id: str | None,
        capability: ModelCapability,
        port: EmbeddingModel | ChatModel,
        status: InvocationStatus,
        latency_ms: int,
        error_type: str | None = None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        """在独立短事务中持久化调用记录；失败只记日志。"""
        invocation = create_model_invocation(
            run_id=run_id,
            capability=capability,
            provider=port.provider,
            model=model or port.model,
            status=status,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error_type=error_type,
        )
        try:
            async with self._session_factory() as session:
                await self._invocation_repo_factory(session).add(invocation)
                await session.commit()
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "model_invocation_record_failed",
                exc=exc,
                operation=capability.value,
                provider=port.provider,
                model=model or port.model,
                run_id=run_id,
                error_code=type(exc).__name__,
            )
