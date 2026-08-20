"""Project 相关的 HTTP 路由。"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from literature_agent.api.dependencies import ActorDep
from literature_agent.application.project_service import ProjectService
from literature_agent.domain.exceptions import ProjectNotFoundError
from literature_agent.domain.project import MAX_NAME_LENGTH
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    """创建 Project 的请求体。"""

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(default="", max_length=4000)


class ProjectResponse(BaseModel):
    """Project 的响应模型。"""

    project_id: str
    owner_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


async def get_project_service(request: Request) -> ProjectService:
    """从应用状态构建 ProjectService。"""
    app_state = request.app.state.app_state
    return ProjectService(
        session_factory=app_state.session_factory,
        repo_factory=SqlalchemyProjectRepository,
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
) -> list[ProjectResponse]:
    """列出当前 actor 的所有 Project。"""
    projects = await service.list_projects(actor)
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
