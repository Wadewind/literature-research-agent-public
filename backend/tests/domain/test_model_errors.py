"""模型调用错误分类测试（切片 3）。"""

from literature_agent.domain.model_errors import (
    ModelAuthError,
    ModelInvalidRequestError,
    ModelRateLimitError,
    ModelResponseError,
    ModelServerError,
    ModelTimeoutError,
)
from literature_agent.domain.retry_policy import is_permanent_error


def test_permanent_model_errors() -> None:
    """认证、非法请求与响应形状错误判定为永久错误（不重试）。"""
    assert is_permanent_error(ModelAuthError("401"))
    assert is_permanent_error(ModelInvalidRequestError("400"))
    assert is_permanent_error(ModelResponseError("响应缺 data 字段"))


def test_temporary_model_errors() -> None:
    """限流、服务端与超时错误判定为临时错误（可重试）。"""
    assert not is_permanent_error(ModelRateLimitError("429"))
    assert not is_permanent_error(ModelServerError("500"))
    assert not is_permanent_error(ModelTimeoutError("超时"))
