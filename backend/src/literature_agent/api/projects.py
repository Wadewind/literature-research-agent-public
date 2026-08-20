"""Project 相关的 HTTP 路由。"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Self

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.project_service import ProjectService
from literature_agent.domain.exceptions import (
    ProjectArchivedError,
    ProjectHasActiveRunsError,
    ProjectNotFoundError,
)
from literature_agent.domain.project import MAX_NAME_LENGTH
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    """创建 Project 的请求体。"""

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(default="", max_length=4000)


class ProjectUpdateRequest(BaseModel):
    """修改 Project 的请求体，至少提供一个字段。"""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> Self:
        """名称与说明至少提供一个。"""
        if self.name is None and self.description is None:
            raise ValueError("至少需要提供一个待修改字段")
        return self


class ProjectResponse(BaseModel):
    """Project 的响应模型。"""

    project_id: str
    owner_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


async def get_project_service(request: Request) -> ProjectService:
    """从应用状态构建 ProjectService。"""
    app_state = request.app.state.app_state
    return ProjectService(
        session_factory=app_state.session_factory,
        repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
    )


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectResponse)
async def create_project(
    body: ProjectCreateRequest,
    actor: ActorDep,
    service: ProjectServiceDep,
) -> ProjectResponse:
    """为当前 actor 创建 Project。"""
    project = await service.create_project(actor, body.name, body.description)
    return ProjectResponse(**asdict(project))


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    actor: ActorDep,
    service: ProjectServiceDep,
    include_archived: Annotated[bool, Query()] = False,
) -> list[ProjectResponse]:
    """列出当前 actor 的 Project；默认只返回 active。"""
    projects = await service.list_projects(actor, include_archived)
    return [ProjectResponse(**asdict(project)) for project in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    actor: ActorDep,
    service: ProjectServiceDep,
) -> ProjectResponse:
    """获取当前 actor 可见的单个 Project。"""
    try:
        project = await service.get_project(actor, project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    return ProjectResponse(**asdict(project))


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: ProjectUpdateRequest,
    actor: ActorDep,
    service: ProjectServiceDep,
) -> ProjectResponse:
    """修改 Project 的名称与说明；已归档 Project 拒绝修改。"""
    try:
        project = await service.update_project(
            actor, project_id, name=body.name, description=body.description
        )
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    except ProjectArchivedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project_archived",
        ) from None
    return ProjectResponse(**asdict(project))


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: str,
    actor: ActorDep,
    service: ProjectServiceDep,
) -> ProjectResponse:
    """归档 Project；幂等，存在非终态 Run 时返回 409。"""
    try:
        project = await service.archive_project(actor, project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    except ProjectHasActiveRunsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project_has_active_runs",
        ) from None
    return ProjectResponse(**asdict(project))


@router.post("/{project_id}/restore", response_model=ProjectResponse)
async def restore_project(
    project_id: str,
    actor: ActorDep,
    service: ProjectServiceDep,
) -> ProjectResponse:
    """恢复已归档 Project；幂等。"""
    try:
        project = await service.restore_project(actor, project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project 不存在",
        ) from None
    return ProjectResponse(**asdict(project))
