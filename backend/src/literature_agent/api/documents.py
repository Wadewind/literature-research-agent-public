"""文档内容查询相关的 HTTP 路由。"""

from datetime import datetime
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.document_query_service import (
    DocumentOverview,
    DocumentQueryService,
    ElementView,
)
from literature_agent.domain.document_element import ElementType
from literature_agent.domain.exceptions import (
    DocumentNotReadyError,
    PaperVersionNotFoundError,
    ProjectNotFoundError,
)
from literature_agent.infrastructure.persistence.element_repository import (
    SqlalchemyElementRepository,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.parse_revision_repository import (
    SqlalchemyParseRevisionRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)

router = APIRouter(prefix="/api/v1/projects", tags=["documents"])


class SectionResponse(BaseModel):
    """章节概览条目。"""

    section_path: str
    title: str


class DocumentResponse(BaseModel):
    """文档当前 Revision 概览。"""

    revision_id: str
    parser_name: str
    parser_version: str
    parser_profile_hash: str
    status: str
    completed_at: datetime | None
    element_count: int
    degraded: bool
    warnings: list[str]
    sections: list[SectionResponse]


class SourceLocationResponse(BaseModel):
    """Element 来源定位。"""

    page: int
    bbox: list[float] | None
    parser_ref: str | None
    char_range: list[int] | None


class ElementResponse(BaseModel):
    """Element 及其来源定位。"""

    element_id: str
    element_type: str
    sequence: int
    parent_element_id: str | None
    section_path: str | None
    text: str | None
    payload: dict
    content_hash: str
    warnings: list[str]
    locations: list[SourceLocationResponse]


async def get_document_query_service(request: Request) -> DocumentQueryService:
    """从应用状态构建 DocumentQueryService。"""
    app_state = request.app.state.app_state
    return DocumentQueryService(
        session_factory=app_state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        element_repo_factory=SqlalchemyElementRepository,
    )


DocumentQueryServiceDep = Annotated[DocumentQueryService, Depends(get_document_query_service)]


def _document_to_response(overview: DocumentOverview) -> DocumentResponse:
    """将 DocumentOverview 转换为响应模型。"""
    return DocumentResponse(
        revision_id=overview.revision_id,
        parser_name=overview.parser_name,
        parser_version=overview.parser_version,
        parser_profile_hash=overview.parser_profile_hash,
        status=overview.status,
        completed_at=overview.completed_at,
        element_count=overview.element_count,
        degraded=overview.degraded,
        warnings=overview.warnings,
        sections=[
            SectionResponse(section_path=s.section_path, title=s.title) for s in overview.sections
        ],
    )


def _element_to_response(view: ElementView) -> ElementResponse:
    """将 ElementView 转换为响应模型。"""
    element = view.element
    return ElementResponse(
        element_id=element.element_id,
        element_type=element.element_type.value,
        sequence=element.sequence,
        parent_element_id=element.parent_element_id,
        section_path=element.section_path,
        text=element.text,
        payload=element.payload,
        content_hash=element.content_hash,
        warnings=list(element.warnings),
        locations=[
            SourceLocationResponse(
                page=loc.page,
                bbox=loc.bbox,
                parser_ref=loc.parser_ref,
                char_range=loc.char_range,
            )
            for loc in view.locations
        ],
    )


def _handle_query_errors(exc: Exception) -> NoReturn:
    """把领域异常映射为 HTTP 错误。"""
    if isinstance(exc, ProjectNotFoundError | PaperVersionNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="资源不存在",
        ) from None
    if isinstance(exc, DocumentNotReadyError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="document_not_ready",
        ) from None
    raise exc


@router.get(
    "/{project_id}/paper-versions/{version_id}/document",
    response_model=DocumentResponse,
)
async def get_document(
    project_id: str,
    version_id: str,
    actor: ActorDep,
    service: DocumentQueryServiceDep,
) -> DocumentResponse:
    """获取文档当前 Parse Revision 概览。"""
    try:
        overview = await service.get_document(actor, project_id, version_id)
    except (ProjectNotFoundError, PaperVersionNotFoundError, DocumentNotReadyError) as exc:
        _handle_query_errors(exc)
    return _document_to_response(overview)


@router.get(
    "/{project_id}/paper-versions/{version_id}/elements",
    response_model=list[ElementResponse],
)
async def list_elements(
    project_id: str,
    version_id: str,
    actor: ActorDep,
    service: DocumentQueryServiceDep,
    page: Annotated[int | None, Query(ge=1)] = None,
    section: Annotated[str | None, Query(max_length=255)] = None,
    type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ElementResponse]:
    """按页码/章节/类型过滤查询 Element 及来源定位。"""
    if type is not None and type not in {t.value for t in ElementType}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法 element type: {type}",
        )
    try:
        views = await service.list_elements(
            actor,
            project_id,
            version_id,
            page=page,
            section_prefix=section,
            element_type=type,
            limit=limit,
            offset=offset,
        )
    except (ProjectNotFoundError, PaperVersionNotFoundError, DocumentNotReadyError) as exc:
        _handle_query_errors(exc)
    return [_element_to_response(view) for view in views]
