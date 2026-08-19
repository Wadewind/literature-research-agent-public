"""文献上传相关的 HTTP 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.ingestion_service import IngestionService, UploadResult
from literature_agent.domain.exceptions import (
    FileValidationError,
    IdempotencyConflictError,
    ProjectNotFoundError,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
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
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)

router = APIRouter(prefix="/api/v1/projects", tags=["paper-files"])


def get_ingestion_service(request: Request) -> IngestionService:
    """从应用状态构建 IngestionService。"""
    app_state = request.app.state.app_state
    return IngestionService(
        max_upload_size_bytes=app_state.settings.max_upload_size_bytes,
        session_factory=app_state.session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        storage=app_state.storage,
    )


IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]


@router.post(
    "/{project_id}/paper-files",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadResult,
)
async def upload_paper_file(
    project_id: str,
    actor: ActorDep,
    service: IngestionServiceDep,
    file: Annotated[UploadFile, File(...)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UploadResult:
    """上传 PDF 到指定 Project，创建 Paper/PaperVersion 和 Ingestion Run。"""
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少 Idempotency-Key 请求头",
        )

    content = await file.read()
    filename = file.filename or "upload.pdf"
    content_type = file.content_type or "application/octet-stream"

    try:
        return await service.upload_paper_file(
            actor=actor,
            project_id=project_id,
            filename=filename,
            content_type=content_type,
            content=content,
            idempotency_key=idempotency_key,
            correlation_id="api-upload-paper-file",
        )
    except FileValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
