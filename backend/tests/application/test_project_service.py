"""Project Application Service 测试。"""

import pytest

from literature_agent.application.project_service import ProjectNotFoundError, ProjectService
from literature_agent.domain.actor import ActorContext
from tests.fakes.fake_project_repository import (
    FakeProjectRepository,
    fake_repo_factory,
    fake_session,
)


@pytest.fixture
def service() -> ProjectService:
    """提供使用 Fake Repository 的 ProjectService。"""
    fake_repo_factory.repo = FakeProjectRepository()
    return ProjectService(session_factory=fake_session, repo_factory=fake_repo_factory)


@pytest.mark.asyncio
async def test_create_project(service: ProjectService) -> None:
    """创建 Project 后应能按 ID 查询到。"""
    actor = ActorContext(owner_id="user-1")

    project = await service.create_project(actor, "我的项目", "描述")

    assert project.owner_id == "user-1"
    assert project.name == "我的项目"
    fetched = await service.get_project(actor, project.project_id)
    assert fetched.project_id == project.project_id


@pytest.mark.asyncio
async def test_list_projects_only_returns_owned(service: ProjectService) -> None:
    """列表只返回当前 actor 拥有的 Project。"""
    actor_a = ActorContext(owner_id="user-a")
    actor_b = ActorContext(owner_id="user-b")
    project_a = await service.create_project(actor_a, "A 的项目", "")
    await service.create_project(actor_b, "B 的项目", "")

    results = await service.list_projects(actor_a)

    assert len(results) == 1
    assert results[0].project_id == project_a.project_id


@pytest.mark.asyncio
async def test_get_project_not_found(service: ProjectService) -> None:
    """查询不存在的 Project 应抛 ProjectNotFoundError。"""
    actor = ActorContext(owner_id="user-1")

    with pytest.raises(ProjectNotFoundError):
        await service.get_project(actor, "00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_get_project_owned_by_other_user(service: ProjectService) -> None:
    """不能查看其他用户的 Project。"""
    actor_a = ActorContext(owner_id="user-a")
    actor_b = ActorContext(owner_id="user-b")
    project = await service.create_project(actor_a, "A 的项目", "")

    with pytest.raises(ProjectNotFoundError):
        await service.get_project(actor_b, project.project_id)
