"""FastAPI 应用工厂。"""

from fastapi import FastAPI

from literature_agent.api.conversations import router as conversations_router
from literature_agent.api.documents import router as documents_router
from literature_agent.api.health import router as health_router
from literature_agent.api.paper_files import router as paper_files_router
from literature_agent.api.papers import router as papers_router
from literature_agent.api.projects import router as projects_router
from literature_agent.api.runs import router as runs_router
from literature_agent.infrastructure.lifespan import app_lifespan


def create_app() -> FastAPI:
    """创建并配置一个 FastAPI 应用实例。

    工厂本身无状态：所有应用级资源都由 lifespan 上下文管理器创建和回收。
    这样测试不会依赖全局单例，也便于重复创建应用实例。
    """
    app = FastAPI(
        title="Literature Review Agent",
        lifespan=app_lifespan,
    )
    app.include_router(health_router, prefix="/health")
    app.include_router(projects_router)
    app.include_router(runs_router)
    app.include_router(paper_files_router)
    app.include_router(papers_router)
    app.include_router(documents_router)
    app.include_router(conversations_router)
    return app
