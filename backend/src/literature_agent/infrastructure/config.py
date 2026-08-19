"""应用配置。"""

import os
from dataclasses import dataclass, field

_DEFAULT_APP_NAME = "Literature Review Agent"
_DEFAULT_DATABASE_URL = "postgresql+psycopg://agent:agent@127.0.0.1:5432/agent_service"
_DEFAULT_DEV_ACTOR_ID = "dev-user"


@dataclass(frozen=True, slots=True)
class Settings:
    """不可变的应用配置。

    配置值从 ``AGENT_`` 前缀的环境变量读取，或使用本地开发的合理默认值。
    本类不引入额外依赖，因此可以在 lifespan 启动时构造，
    而不必依赖框架专属的配置库。
    """

    app_name: str = field(default=_DEFAULT_APP_NAME)
    debug: bool = field(default=False)
    database_url: str = field(default=_DEFAULT_DATABASE_URL)
    dev_actor_id: str = field(default=_DEFAULT_DEV_ACTOR_ID)

    @classmethod
    def from_env(cls) -> "Settings":
        """根据环境变量创建设置对象。"""
        return cls(
            app_name=os.getenv("AGENT_APP_NAME", _DEFAULT_APP_NAME),
            debug=os.getenv("AGENT_DEBUG", "").lower() in {"1", "true", "yes"},
            database_url=os.getenv("AGENT_DATABASE_URL", _DEFAULT_DATABASE_URL),
            dev_actor_id=os.getenv("AGENT_DEV_ACTOR_ID", _DEFAULT_DEV_ACTOR_ID),
        )
