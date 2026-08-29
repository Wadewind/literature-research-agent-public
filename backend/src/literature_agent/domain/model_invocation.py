"""Model Invocation 领域实体。

记录一次模型调用的审计信息：能力、Provider、模型、状态、token 用量、
延迟与错误分类。按安全约定不保存 Prompt 或响应内容。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from literature_agent.domain.model_types import ChatFinishReason


class ModelCapability(StrEnum):
    """模型能力类型。"""

    EMBEDDING = "embedding"
    CHAT = "chat"


class InvocationStatus(StrEnum):
    """调用结果状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ModelInvocation:
    """一次模型调用记录。

    属性:
        invocation_id: 记录标识符。
        run_id: 关联的 Run 标识符，未接线执行器时为 None。
        capability: 能力类型（embedding/chat）。
        provider: Provider 名称（如 zhipu/deepseek/fake）。
        model: 模型名。
        status: 调用结果状态。
        latency_ms: 调用延迟（毫秒）。
        created_at: 记录创建时间（UTC）。
        prompt_tokens: 输入 token 数，未知为 None。
        completion_tokens: 输出 token 数，未知为 None。
        error_type: 失败时的异常类型名，成功为 None。
        requested_max_tokens: 本次请求的输出上限，未设置为 None。
        finish_reason: allowlist 化的 Chat 终止原因。
        response_bytes: Chat content 的 UTF-8 字节数，不保存正文。
        response_sha256: Chat content 的 SHA-256，不保存正文。
    """

    invocation_id: str
    run_id: str | None
    capability: ModelCapability
    provider: str
    model: str
    status: InvocationStatus
    latency_ms: int
    created_at: datetime
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_type: str | None = None
    requested_max_tokens: int | None = None
    finish_reason: ChatFinishReason | None = None
    response_bytes: int | None = None
    response_sha256: str | None = None


def create_model_invocation(
    *,
    run_id: str | None,
    capability: ModelCapability,
    provider: str,
    model: str,
    status: InvocationStatus,
    latency_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error_type: str | None = None,
    requested_max_tokens: int | None = None,
    finish_reason: ChatFinishReason | None = None,
    response_bytes: int | None = None,
    response_sha256: str | None = None,
) -> ModelInvocation:
    """创建一条模型调用记录。"""
    return ModelInvocation(
        invocation_id=str(uuid4()),
        run_id=run_id,
        capability=capability,
        provider=provider,
        model=model,
        status=status,
        latency_ms=latency_ms,
        created_at=datetime.now(UTC),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error_type=error_type,
        requested_max_tokens=requested_max_tokens,
        finish_reason=finish_reason,
        response_bytes=response_bytes,
        response_sha256=response_sha256,
    )
