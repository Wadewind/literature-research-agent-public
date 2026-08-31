"""Project 文献库的 arXiv 搜索与引入 HTTP 路由。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from literature_agent.api.dependencies import ActorDep, CorrelationIdDep
from literature_agent.api.paper_files import get_ingestion_service
from literature_agent.application.ingestion_service import IngestionService, UploadResult
from literature_agent.application.project_arxiv_library_service import (
    ProjectArxivLibraryService,
)
from literature_agent.domain.arxiv import ArxivError, ArxivQueryValidationError, ArxivSearchQuery
from literature_agent.domain.exceptions import (
    FileValidationError,
    IdempotencyConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)

router = APIRouter(prefix="/api/v1/projects", tags=["project-arxiv"])


class ArxivPaperResponse(BaseModel):
    """不暴露下载 URL 的 arXiv 搜索结果。"""

    arxiv_id: str
    arxiv_version: str
    versioned_id: str
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    published_at: datetime
    updated_at: datetime


class ImportArxivPaperRequest(BaseModel):
    """用户从服务端搜索结果选择的版本化 arXiv ID。"""

    versioned_arxiv_id: str = Field(min_length=3, max_length=80)


async def get_project_arxiv_library_service(
    request: Request,
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> ProjectArxivLibraryService:
    """从应用状态构建 Project 文献库 arXiv 用例。"""
    app_state = request.app.state.app_state
    return ProjectArxivLibraryService(
        session_factory=app_state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        arxiv_gateway=app_state.arxiv_gateway,
        ingestion_service=ingestion_service,
        max_download_bytes=app_state.settings.max_upload_size_bytes,
    )


ProjectArxivServiceDep = Annotated[
    ProjectArxivLibraryService, Depends(get_project_arxiv_library_service)
]


@router.get("/{project_id}/arxiv/search", response_model=list[ArxivPaperResponse])
async def search_arxiv_papers(
    project_id: str,
    actor: ActorDep,
    service: ProjectArxivServiceDep,
    q: Annotated[str, Query(min_length=1, max_length=1024)],
    max_results: Annotated[int, Query(ge=1, le=20)] = 10,
) -> list[ArxivPaperResponse]:
    """在 Project 授权边界内执行受限 arXiv 搜索。"""
    try:
        papers = await service.search(
            actor=actor,
            project_id=project_id,
            query=ArxivSearchQuery(expression=q, max_results=max_results),
        )
        return [
            ArxivPaperResponse(
                arxiv_id=paper.arxiv_id,
                arxiv_version=paper.arxiv_version,
                versioned_id=paper.versioned_id,
                title=paper.title,
                abstract=paper.abstract,
                authors=list(paper.authors),
                categories=list(paper.categories),
                published_at=paper.published_at,
                updated_at=paper.updated_at,
            )
            for paper in papers
        ]
    except ArxivQueryValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project 不存在"
        ) from None
    except ProjectArchivedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="project_archived"
        ) from None
    except ArxivError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.temporary
            else status.HTTP_502_BAD_GATEWAY,
            detail=exc.code,
        ) from exc


@router.post(
    "/{project_id}/arxiv/import",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadResult,
)
async def import_arxiv_paper(
    project_id: str,
    payload: ImportArxivPaperRequest,
    actor: ActorDep,
    service: ProjectArxivServiceDep,
    response: Response,
    correlation_id: CorrelationIdDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UploadResult:
    """下载选中的官方 arXiv PDF，并复用普通文献 Ingestion。"""
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少 Idempotency-Key 请求头",
        )
    try:
        result = await service.import_paper(
            actor=actor,
            project_id=project_id,
            versioned_arxiv_id=payload.versioned_arxiv_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        if result.already_added:
            response.status_code = status.HTTP_200_OK
        elif result.reused:
            response.status_code = status.HTTP_201_CREATED
        return result
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project 不存在"
        ) from None
    except ProjectArchivedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="project_archived"
        ) from None
    except (FileValidationError, ArxivError) as exc:
        detail = exc.code if isinstance(exc, ArxivError) else str(exc)
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if isinstance(exc, ArxivError) and exc.temporary
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
