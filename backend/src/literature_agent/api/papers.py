"""Paper 列表与 PDF 文件预览相关的 HTTP 路由（切片 10）。"""

from datetime import datetime
from typing import Annotated, NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.paper_query_service import PaperQueryService
from literature_agent.application.ports.storage import StorageError
from literature_agent.domain.exceptions import (
    PaperVersionNotFoundError,
    ProjectNotFoundError,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)

router = APIRouter(prefix="/api/v1/projects", tags=["papers"])


class LatestVersionResponse(BaseModel):
    """Paper 最新 Version 摘要。"""

    version_id: str
    display_filename: str
    size_bytes: int
    created_at: datetime
    parse_ready: bool


class PaperListItemResponse(BaseModel):
    """Paper 列表条目。"""

    paper_id: str
    created_at: datetime
    latest_version: LatestVersionResponse | None


def get_paper_query_service(request: Request) -> PaperQueryService:
    """从应用状态构建 PaperQueryService。"""
    app_state = request.app.state.app_state
    return PaperQueryService(
        session_factory=app_state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        storage=app_state.storage,
    )


PaperQueryServiceDep = Annotated[PaperQueryService, Depends(get_paper_query_service)]


def _handle_query_errors(exc: Exception) -> NoReturn:
    """把领域异常映射为 HTTP 错误；越权与不存在统一 404。"""
    if isinstance(exc, ProjectNotFoundError | PaperVersionNotFoundError | StorageError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="资源不存在",
        ) from None
    raise exc


@router.get("/{project_id}/papers", response_model=list[PaperListItemResponse])
async def list_papers(
    project_id: str,
    actor: ActorDep,
    service: PaperQueryServiceDep,
) -> list[PaperListItemResponse]:
    """列出当前 actor 可见 Project 的全部 Paper 及最新 Version 摘要。"""
    try:
        items = await service.list_papers(actor, project_id)
    except ProjectNotFoundError as exc:
        _handle_query_errors(exc)
    return [
        PaperListItemResponse(
            paper_id=item.paper_id,
            created_at=item.created_at,
            latest_version=(
                LatestVersionResponse(
                    version_id=item.latest_version.version_id,
                    display_filename=item.latest_version.display_filename,
                    size_bytes=item.latest_version.size_bytes,
                    created_at=item.latest_version.created_at,
                    parse_ready=item.latest_version.parse_ready,
                )
                if item.latest_version is not None
                else None
            ),
        )
        for item in items
    ]


@router.get("/{project_id}/paper-versions/{version_id}/file")
async def get_version_file(
    project_id: str,
    version_id: str,
    actor: ActorDep,
    service: PaperQueryServiceDep,
) -> Response:
    """返回 Version 对应的 PDF 文件内容，供浏览器内联预览。"""
    try:
        result = await service.get_version_file(actor, project_id, version_id)
    except (ProjectNotFoundError, PaperVersionNotFoundError, StorageError) as exc:
        _handle_query_errors(exc)
    quoted = quote(result.version.display_filename)
    return Response(
        content=result.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quoted}"},
    )
