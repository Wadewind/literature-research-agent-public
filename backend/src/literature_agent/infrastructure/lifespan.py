"""FastAPI lifespan 与应用级资源管理。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from literature_agent.infrastructure.config import Settings


@dataclass
class AppState:
    """应用运行期间存活资源的容器。

    lifespan 上下文管理器在启动时创建一个实例并交给 FastAPI，
    FastAPI 将其存入 ``app.state``。后续切片会在这里加入数据库引擎、
    队列客户端等适配器。
    """

    settings: Settings


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[dict[str, AppState]]:
    """管理应用级资源的启动与关闭。

    参数:
        app: FastAPI 应用实例。

    产出:
        一个映射，其 ``app_state`` 键存放已填充的 ``AppState``。
        FastAPI 会将该映射合并到 ``app.state``。
    """
    state = AppState(settings=Settings.from_env())
    try:
        yield {"app_state": state}
    finally:
        pass
