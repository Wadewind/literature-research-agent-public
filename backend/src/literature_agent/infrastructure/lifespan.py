"""FastAPI lifespan 与应用级资源管理。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from literature_agent.application.ports.run_queue import RunQueue
from literature_agent.application.ports.storage import Storage
from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.persistence.database import (
    create_engine,
    create_session_factory,
)
from literature_agent.infrastructure.queue.arq_run_queue import ArqRunQueue
from literature_agent.infrastructure.storage.local_storage import LocalFileStorage


@dataclass
class AppState:
    """应用运行期间存活资源的容器。

    lifespan 上下文管理器在启动时创建一个实例并交给 FastAPI，
    FastAPI 将其存入 ``app.state``。

    ``queue`` 的 Valkey 连接在首次投递时惰性建立，
    因此 API 启动不强依赖 Valkey 可用。
    """

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker
    storage: Storage
    queue: RunQueue


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[dict[str, AppState]]:
    """管理应用级资源的启动与关闭。

    参数:
        app: FastAPI 应用实例。

    产出:
        一个映射，其 ``app_state`` 键存放已填充的 ``AppState``。
        FastAPI 会将该映射合并到 ``app.state``。
    """
    settings = Settings.from_env()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    storage = LocalFileStorage(settings.storage_root)
    queue = ArqRunQueue(settings.redis_url)
    state = AppState(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        storage=storage,
        queue=queue,
    )
    try:
        yield {"app_state": state}
    finally:
        await queue.aclose()
        await engine.dispose()
