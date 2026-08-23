"""可信本地开发环境的 Prometheus 指标端点。"""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST

from literature_agent.metrics import metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def expose_metrics() -> Response:
    """只导出当前 API 进程的内存指标，不汇聚 Worker 指标。"""
    return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)
