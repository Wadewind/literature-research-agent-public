"""应用配置。"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Settings:
    """不可变的应用配置。

    配置值从 ``AGENT_`` 前缀的环境变量读取，或使用本地开发的合理默认值。
    本类不引入额外依赖，因此可以在 lifespan 启动时构造，
    而不必依赖框架专属的配置库。
    """

    app_name: str = field(default="Literature Review Agent")
    debug: bool = field(default=False)

    @classmethod
    def from_env(cls) -> "Settings":
        """根据环境变量创建设置对象。"""
        return cls(
            app_name=os.getenv("AGENT_APP_NAME", cls.app_name),
            debug=os.getenv("AGENT_DEBUG", "").lower() in {"1", "true", "yes"},
        )
