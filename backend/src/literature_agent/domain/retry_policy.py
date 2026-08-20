"""错误分类与重试策略（2026-08-20 定稿，最小两类）。

永久错误不再重试（重跑结果必然相同）；临时错误在预算内
按 Outbox 退避参数重试。分类表集中在这里，不引入错误码注册表。
"""

from literature_agent.domain.exceptions import (
    FileValidationError,
    InvalidPdfInputError,
)
from literature_agent.domain.model_errors import (
    ModelAuthError,
    ModelInvalidRequestError,
    ModelResponseError,
)
from literature_agent.domain.queue_outbox import compute_dispatch_backoff

# 永久错误类型：输入类问题与模型认证/非法请求/响应形状错误，重试无意义
_PERMANENT_TYPES: tuple[type[Exception], ...] = (
    InvalidPdfInputError,
    FileValidationError,
    ModelAuthError,
    ModelInvalidRequestError,
    ModelResponseError,
)

# 临时错误类型示例：超时与资源类，其余未知异常也按临时处理
# （保守地给未知错误留重试机会，但受预算限制）


def is_permanent_error(exc: BaseException) -> bool:
    """判断异常是否为永久错误（不应重试）。

    参数:
        exc: 执行过程中抛出的异常。
    """
    return isinstance(exc, _PERMANENT_TYPES)


def compute_retry_backoff(attempt_number: int) -> float:
    """根据已用尝试次数计算下一次重试前的退避秒数。

    复用 Outbox 派发退避：1s 起指数增长，上限 60s，避免两套参数漂移。

    参数:
        attempt_number: 已发生的尝试次数（从 1 开始）。
    """
    return compute_dispatch_backoff(attempt_number).total_seconds()
