"""错误分类与重试退避策略测试。"""

from literature_agent.domain.exceptions import (
    CheckpointDataError,
    CheckpointUnavailableError,
    EvidenceMatrixInvalidError,
    EvidenceMatrixScopeError,
    FileValidationError,
    InvalidPdfInputError,
    ParserResourceError,
)
from literature_agent.domain.retry_policy import (
    compute_retry_backoff,
    is_permanent_error,
)


def test_permanent_input_errors() -> None:
    """输入类错误判定为永久错误。"""
    assert is_permanent_error(InvalidPdfInputError("损坏"))
    assert is_permanent_error(FileValidationError("类型不支持"))
    assert is_permanent_error(CheckpointDataError("checkpoint 无效"))
    assert is_permanent_error(EvidenceMatrixScopeError("范围非法"))
    assert is_permanent_error(EvidenceMatrixInvalidError())


def test_temporary_errors() -> None:
    """超时、资源与未知异常判定为临时错误。"""
    assert not is_permanent_error(TimeoutError("超时"))
    assert not is_permanent_error(ParserResourceError("内存不足"))
    assert not is_permanent_error(ValueError("未知"))
    assert not is_permanent_error(ConnectionError("网络断开"))
    assert not is_permanent_error(CheckpointUnavailableError("数据库暂不可用"))


def test_retry_backoff_exponential_with_cap() -> None:
    """退避 1s 起指数增长，封顶 60s。"""
    assert compute_retry_backoff(1) == 1.0
    assert compute_retry_backoff(2) == 2.0
    assert compute_retry_backoff(3) == 4.0
    assert compute_retry_backoff(10) == 60.0
    assert compute_retry_backoff(100) == 60.0
