"""模型调用错误分类（Phase 2 切片 3，2026-08-20 定稿）。

临时错误（429/5xx/网络超时）由 Provider Adapter 层做有限短重试，
耗尽后交 Run 层按预算 RETRY_WAIT；永久错误（认证/非法请求/响应形状）
不重试——响应形状的结构修复属于上层职责，不在 Adapter 层重试。

错误消息只保留截断后的安全描述，不含完整 Prompt 或响应体。
"""


class ModelError(Exception):
    """模型调用失败基类。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ModelRateLimitError(ModelError):
    """Provider 限流（HTTP 429），Adapter 短重试耗尽后抛出（临时）。"""


class ModelServerError(ModelError):
    """Provider 服务端错误（HTTP 5xx）或网络连接失败（临时）。"""


class ModelTimeoutError(ModelError):
    """模型请求网络超时（临时）。"""


class ModelAuthError(ModelError):
    """认证失败（HTTP 401/403）或缺少 API Key（永久）。"""


class ModelInvalidRequestError(ModelError):
    """请求非法（HTTP 400 等其余 4xx），重试结果必然相同（永久）。"""


class ModelResponseError(ModelError):
    """响应形状非法：JSON 畸形或缺少约定字段（永久）。

    Adapter 层不做结构修复重试，由上层（结构化输出校验）处理。
    """
