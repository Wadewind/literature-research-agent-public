"""Actor Context 依赖提供。"""

from fastapi import Request

from literature_agent.domain.actor import ActorContext


def get_actor(request: Request) -> ActorContext:
    """从应用状态中提取当前可信 Actor。

    当前实现从 ``Settings.dev_actor_id`` 读取本地开发用户。
    生产环境必须替换为真实的认证依赖，避免意外启用开发用户。

    参数:
        request: FastAPI 请求对象，用于访问应用状态中的配置。

    返回:
        当前请求的 ``ActorContext``。
    """
    settings = request.app.state.app_state.settings
    return ActorContext(owner_id=settings.dev_actor_id)
