"""Project Application Service 测试。"""

from dataclasses import replace

import pytest

from literature_agent.application.project_service import ProjectNotFoundError, ProjectService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    ProjectArchivedError,
    ProjectHasActiveRunsError,
)
from literature_agent.domain.run import RunStatus, create_run
from tests.fakes.fake_project_repository import (
    FakeProjectRepository,
    fake_repo_factory,
    fake_session,
)
from tests.fakes.fake_run_repository import FakeRunRepository


@pytest.fixture
def run_repo() -> FakeRunRepository:
    """提供 Fake Run Repository。"""
    return FakeRunRepository()


@pytest.fixture
def service(run_repo: FakeRunRepository) -> ProjectService:
    """提供使用 Fake Repository 的 ProjectService。"""
    fake_repo_factory.repo = FakeProjectRepository()
    return ProjectService(
        session_factory=fake_session,
        repo_factory=fake_repo_factory,
        run_repo_factory=lambda _session: run_repo,
    )


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


@pytest.mark.asyncio
async def test_update_project_changes_fields(service: ProjectService) -> None:
    """update_project 修改名称和说明。"""
    actor = ActorContext(owner_id="user-1")
    project = await service.create_project(actor, "旧名称", "旧说明")

    updated = await service.update_project(actor, project.project_id, name="新名称")

    assert updated.name == "新名称"
    assert updated.description == "旧说明"
    fetched = await service.get_project(actor, project.project_id)
    assert fetched.name == "新名称"


@pytest.mark.asyncio
async def test_update_project_not_found_or_forbidden(service: ProjectService) -> None:
    """修改不存在或越权的 Project 抛 ProjectNotFoundError。"""
    actor_a = ActorContext(owner_id="user-a")
    actor_b = ActorContext(owner_id="user-b")
    project = await service.create_project(actor_a, "A 的项目", "")

    with pytest.raises(ProjectNotFoundError):
        await service.update_project(actor_b, project.project_id, name="越权改名")
    with pytest.raises(ProjectNotFoundError):
        await service.update_project(actor_a, "missing", name="不存在")


@pytest.mark.asyncio
async def test_update_archived_project_rejected(service: ProjectService) -> None:
    """已归档 Project 拒绝修改。"""
    actor = ActorContext(owner_id="user-1")
    project = await service.create_project(actor, "项目", "")
    await service.archive_project(actor, project.project_id)

    with pytest.raises(ProjectArchivedError):
        await service.update_project(actor, project.project_id, name="新名称")


@pytest.mark.asyncio
async def test_archive_and_restore_project(service: ProjectService) -> None:
    """归档后恢复，状态往返正确。"""
    actor = ActorContext(owner_id="user-1")
    project = await service.create_project(actor, "项目", "")

    archived = await service.archive_project(actor, project.project_id)
    assert archived.is_archived is True
    fetched = await service.get_project(actor, project.project_id)
    assert fetched.is_archived is True

    restored = await service.restore_project(actor, project.project_id)
    assert restored.is_archived is False
    fetched = await service.get_project(actor, project.project_id)
    assert fetched.is_archived is False


@pytest.mark.asyncio
async def test_archive_is_idempotent(service: ProjectService) -> None:
    """重复归档返回已归档状态，不报错。"""
    actor = ActorContext(owner_id="user-1")
    project = await service.create_project(actor, "项目", "")

    first = await service.archive_project(actor, project.project_id)
    second = await service.archive_project(actor, project.project_id)

    assert second.is_archived is True
    assert second.archived_at == first.archived_at


@pytest.mark.asyncio
async def test_restore_is_idempotent(service: ProjectService) -> None:
    """对 active Project 恢复是幂等成功。"""
    actor = ActorContext(owner_id="user-1")
    project = await service.create_project(actor, "项目", "")

    restored = await service.restore_project(actor, project.project_id)

    assert restored.is_archived is False


@pytest.mark.asyncio
async def test_archive_not_found_or_forbidden(service: ProjectService) -> None:
    """归档/恢复不存在或越权的 Project 抛 ProjectNotFoundError。"""
    actor_a = ActorContext(owner_id="user-a")
    actor_b = ActorContext(owner_id="user-b")
    project = await service.create_project(actor_a, "A 的项目", "")

    with pytest.raises(ProjectNotFoundError):
        await service.archive_project(actor_b, project.project_id)
    with pytest.raises(ProjectNotFoundError):
        await service.restore_project(actor_b, project.project_id)
    with pytest.raises(ProjectNotFoundError):
        await service.archive_project(actor_a, "missing")


@pytest.mark.asyncio
async def test_archive_rejected_with_active_runs(
    service: ProjectService,
    run_repo: FakeRunRepository,
) -> None:
    """存在非终态 Run 时归档被拒绝。"""
    actor = ActorContext(owner_id="user-1")
    project = await service.create_project(actor, "项目", "")
    for status in (
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RETRY_WAIT,
        RunStatus.CANCEL_REQUESTED,
    ):
        run = replace(create_run(project.project_id, actor.owner_id, "ingestion"), status=status)
        await run_repo.add(run)

        with pytest.raises(ProjectHasActiveRunsError):
            await service.archive_project(actor, project.project_id)

        run_repo._runs.clear()


@pytest.mark.asyncio
async def test_archive_allowed_with_only_terminal_runs(
    service: ProjectService,
    run_repo: FakeRunRepository,
) -> None:
    """只有终态 Run 时归档成功。"""
    actor = ActorContext(owner_id="user-1")
    project = await service.create_project(actor, "项目", "")
    for status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
        run = replace(create_run(project.project_id, actor.owner_id, "ingestion"), status=status)
        await run_repo.add(run)

    archived = await service.archive_project(actor, project.project_id)

    assert archived.is_archived is True


@pytest.mark.asyncio
async def test_list_projects_excludes_archived_by_default(service: ProjectService) -> None:
    """默认列表只返回 active Project，include_archived 时全部返回。"""
    actor = ActorContext(owner_id="user-1")
    active = await service.create_project(actor, "活动项目", "")
    archived = await service.create_project(actor, "归档项目", "")
    await service.archive_project(actor, archived.project_id)

    default_list = await service.list_projects(actor)
    full_list = await service.list_projects(actor, include_archived=True)

    assert [p.project_id for p in default_list] == [active.project_id]
    assert len(full_list) == 2
