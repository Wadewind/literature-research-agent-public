"""服务健康检查的 HTTP 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from literature_agent.application.health_service import HealthService

router = APIRouter(tags=["health"])


class LiveResponse(BaseModel):
    """GET /health/live 的响应模型。"""

    status: str


def get_health_service() -> HealthService:
    """提供健康检查应用服务的依赖。"""
    return HealthService()


@router.get("/live", response_model=LiveResponse)
async def live(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> LiveResponse:
    """存活探针。

    只要 API 进程在运行就返回 200 OK。本端点故意不验证外部依赖；
    就绪检查将在后续引入数据库、队列等适配器后单独实现。
    """
    status = service.get_live_status()
    return LiveResponse(status=status.status)
