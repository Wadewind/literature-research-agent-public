"""健康检查的领域值对象。"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """服务的存活或就绪状态。

    属性:
        status: 简短状态字符串，例如 ``ok`` 或 ``degraded``。
    """

    status: str
    dependencies: Mapping[str, str] | None = None
