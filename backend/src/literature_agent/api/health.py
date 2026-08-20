"""服务健康检查的 HTTP 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from literature_agent.application.health_service import HealthService
from literature_agent.infrastructure.readiness import (
    DatabaseReadinessProbe,
    ValkeyReadinessProbe,
)

router = APIRouter(tags=["health"])


class LiveResponse(BaseModel):
    """GET /health/live 的响应模型。"""

    status: str


class ReadyResponse(BaseModel):
    """GET /health/ready 的响应模型。"""

    status: str
    dependencies: dict[str, str]


async def get_health_service() -> HealthService:
    """提供健康检查应用服务的依赖。"""
    return HealthService()


async def get_readiness_service(request: Request) -> HealthService:
    """基于应用级连接配置构造依赖就绪检查服务。"""
    app_state = request.app.state.app_state
    return HealthService(
        probes=(
            DatabaseReadinessProbe(app_state.engine),
            ValkeyReadinessProbe(app_state.settings.redis_url),
        )
    )


@router.get("/live", response_model=LiveResponse)
async def live(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> LiveResponse:
    """存活探针。

    只要 API 进程在运行就返回 200 OK。本端点故意不验证外部依赖。
    """
    status = service.get_live_status()
    return LiveResponse(status=status.status)


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    response: Response,
    service: Annotated[HealthService, Depends(get_readiness_service)],
) -> ReadyResponse:
    """检查 PostgreSQL 与 Valkey；任一不可用时返回 503。"""
    health = await service.get_ready_status()
    if health.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(
        status=health.status,
        dependencies=dict(health.dependencies or {}),
    )
