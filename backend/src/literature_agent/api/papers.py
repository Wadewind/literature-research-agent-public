"""个人文献库、Project 收录与 PDF 预览 HTTP API。"""

from datetime import datetime
from typing import Annotated, NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.paper_query_service import (
    PaperListItem,
    PaperQueryService,
)
from literature_agent.application.paper_service import PaperService
from literature_agent.application.ports.storage import StorageError
from literature_agent.application.project_library_service import ProjectLibraryService
from literature_agent.domain.exceptions import (
    PaperArchivedError,
    PaperNotFoundError,
    PaperVersionNotFoundError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from literature_agent.domain.paper import PaperTitleSource
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)

router = APIRouter(prefix="/api/v1", tags=["papers"])


class VersionSummaryResponse(BaseModel):
    """Paper 固定 Version 的摘要。"""

    version_id: str
    display_filename: str
    size_bytes: int
    created_at: datetime
    parse_ready: bool
    ingestion_run_id: str | None


class PaperListItemResponse(BaseModel):
    """个人文献库或 Project 文献列表条目。"""

    paper_id: str
    title: str | None
    title_source: PaperTitleSource | None
    created_at: datetime
    version: VersionSummaryResponse
    project_ids: list[str]
    archived_at: datetime | None


class PaperStateResponse(BaseModel):
    """Paper 归档状态的响应模型。"""

    paper_id: str
    archived_at: datetime | None


class AddPaperRequest(BaseModel):
    """从个人文献库添加已有 Paper 的请求。"""

    paper_id: str
    version_id: str


class ProjectPaperResponse(BaseModel):
    """Project 收录关系写入结果。"""

    project_id: str
    paper_id: str
    selected_version_id: str
    already_added: bool


async def get_paper_query_service(request: Request) -> PaperQueryService:
    """从应用状态构建 PaperQueryService。"""
    app_state = request.app.state.app_state
    return PaperQueryService(
        session_factory=app_state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        storage=app_state.storage,
    )


async def get_project_library_service(request: Request) -> ProjectLibraryService:
    """从应用状态构建 ProjectLibraryService。"""
    app_state = request.app.state.app_state
    return ProjectLibraryService(
        session_factory=app_state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
    )


async def get_paper_service(request: Request) -> PaperService:
    """从应用状态构建 PaperService。"""
    app_state = request.app.state.app_state
    return PaperService(
        session_factory=app_state.session_factory,
        paper_repo_factory=SqlalchemyPaperRepository,
    )


PaperQueryServiceDep = Annotated[PaperQueryService, Depends(get_paper_query_service)]
ProjectLibraryServiceDep = Annotated[ProjectLibraryService, Depends(get_project_library_service)]
PaperServiceDep = Annotated[PaperService, Depends(get_paper_service)]


def _handle_query_errors(exc: Exception) -> NoReturn:
    """把不存在、越权和 Storage 错误统一映射为 404。"""
    if isinstance(
        exc,
        ProjectNotFoundError | PaperNotFoundError | PaperVersionNotFoundError | StorageError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="资源不存在",
        ) from None
    raise exc


def _handle_archived_errors(exc: Exception) -> NoReturn:
    """把归档冲突映射为 409 稳定业务码。"""
    if isinstance(exc, ProjectArchivedError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project_archived",
        ) from None
    if isinstance(exc, PaperArchivedError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="paper_archived",
        ) from None
    raise exc


def _response(item: PaperListItem) -> PaperListItemResponse:
    """把应用层 Paper 条目转换成 HTTP 响应。"""
    return PaperListItemResponse(
        paper_id=item.paper_id,
        title=item.title,
        title_source=item.title_source,
        created_at=item.created_at,
        version=VersionSummaryResponse(
            version_id=item.version.version_id,
            display_filename=item.version.display_filename,
            size_bytes=item.version.size_bytes,
            created_at=item.version.created_at,
            parse_ready=item.version.parse_ready,
            ingestion_run_id=item.version.ingestion_run_id,
        ),
        project_ids=list(item.project_ids),
        archived_at=item.archived_at,
    )


@router.get("/library/papers", response_model=list[PaperListItemResponse])
async def list_library_papers(
    actor: ActorDep,
    service: PaperQueryServiceDep,
    include_archived: Annotated[bool, Query()] = False,
) -> list[PaperListItemResponse]:
    """列出当前 owner 的个人文献库；默认排除已归档。"""
    return [
        _response(item)
        for item in await service.list_library_papers(actor, include_archived)
    ]


@router.post("/library/papers/{paper_id}/archive", response_model=PaperStateResponse)
async def archive_library_paper(
    paper_id: str,
    actor: ActorDep,
    service: PaperServiceDep,
) -> PaperStateResponse:
    """归档个人文献库 Paper；幂等，不影响已有 Project 收录。"""
    try:
        paper = await service.archive_paper(actor, paper_id)
    except PaperNotFoundError as exc:
        _handle_query_errors(exc)
    return PaperStateResponse(paper_id=paper.paper_id, archived_at=paper.archived_at)


@router.post("/library/papers/{paper_id}/restore", response_model=PaperStateResponse)
async def restore_library_paper(
    paper_id: str,
    actor: ActorDep,
    service: PaperServiceDep,
) -> PaperStateResponse:
    """恢复已归档的个人文献库 Paper；幂等。"""
    try:
        paper = await service.restore_paper(actor, paper_id)
    except PaperNotFoundError as exc:
        _handle_query_errors(exc)
    return PaperStateResponse(paper_id=paper.paper_id, archived_at=paper.archived_at)


@router.get(
    "/projects/{project_id}/papers",
    response_model=list[PaperListItemResponse],
)
async def list_project_papers(
    project_id: str,
    actor: ActorDep,
    service: PaperQueryServiceDep,
) -> list[PaperListItemResponse]:
    """列出 Project 收录的 Paper 与固定 Version。"""
    try:
        items = await service.list_project_papers(actor, project_id)
    except ProjectNotFoundError as exc:
        _handle_query_errors(exc)
    return [_response(item) for item in items]


@router.post(
    "/projects/{project_id}/papers",
    response_model=ProjectPaperResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_existing_paper(
    project_id: str,
    body: AddPaperRequest,
    actor: ActorDep,
    service: ProjectLibraryServiceDep,
    response: Response,
) -> ProjectPaperResponse:
    """把个人文献库中的已有 Version 收录到 Project。"""
    try:
        result = await service.add_existing_paper(actor, project_id, body.paper_id, body.version_id)
    except (ProjectNotFoundError, PaperNotFoundError, PaperVersionNotFoundError) as exc:
        _handle_query_errors(exc)
    except (ProjectArchivedError, PaperArchivedError) as exc:
        _handle_archived_errors(exc)
    if result.already_added:
        response.status_code = status.HTTP_200_OK
    return ProjectPaperResponse(
        project_id=result.relation.project_id,
        paper_id=result.relation.paper_id,
        selected_version_id=result.relation.selected_version_id,
        already_added=result.already_added,
    )


@router.delete(
    "/projects/{project_id}/papers/{paper_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_project_paper(
    project_id: str,
    paper_id: str,
    actor: ActorDep,
    service: ProjectLibraryServiceDep,
) -> Response:
    """从 Project 移除 Paper，但保留个人文献库内容。"""
    try:
        removed = await service.remove_paper(actor, project_id, paper_id)
    except ProjectNotFoundError as exc:
        _handle_query_errors(exc)
    except ProjectArchivedError as exc:
        _handle_archived_errors(exc)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资源不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/paper-versions/{version_id}/file")
async def get_version_file(
    project_id: str,
    version_id: str,
    actor: ActorDep,
    service: PaperQueryServiceDep,
) -> Response:
    """返回 Project 固定 Version 的 PDF 文件。"""
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
