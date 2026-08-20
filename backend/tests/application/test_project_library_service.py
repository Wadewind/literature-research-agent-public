"""个人文献库与 Project 收录服务测试。"""

import pytest

from literature_agent.application.project_library_service import ProjectLibraryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import PaperArchivedError, ProjectArchivedError
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.project import create_project
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session


def _build_service(
    project_repo: FakeProjectRepository,
    paper_repo: FakePaperRepository,
    version_repo: FakePaperVersionRepository,
    relation_repo: FakeProjectPaperRepository,
) -> ProjectLibraryService:
    """基于 Fake Repository 构建 ProjectLibraryService。"""
    return ProjectLibraryService(
        session_factory=fake_session,
        project_repo_factory=lambda _session: project_repo,
        paper_repo_factory=lambda _session: paper_repo,
        paper_version_repo_factory=lambda _session: version_repo,
        project_paper_repo_factory=lambda _session: relation_repo,
    )


@pytest.mark.asyncio
async def test_add_existing_and_remove_only_changes_membership() -> None:
    """添加/移除已有 Paper 只改变 ProjectPaper，不删除文献内容。"""
    project_repo = FakeProjectRepository()
    paper_repo = FakePaperRepository()
    version_repo = FakePaperVersionRepository()
    relation_repo = FakeProjectPaperRepository()
    actor = ActorContext(owner_id="user-1")
    project = create_project(actor.owner_id, "研究项目", "")
    paper = create_paper(actor.owner_id)
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id=actor.owner_id,
        file_hash="hash",
        storage_key="object.pdf",
        size_bytes=42,
        content_type="application/pdf",
    )
    await project_repo.add(project)
    await paper_repo.add(paper)
    await version_repo.add(version)
    service = ProjectLibraryService(
        session_factory=fake_session,
        project_repo_factory=lambda _session: project_repo,
        paper_repo_factory=lambda _session: paper_repo,
        paper_version_repo_factory=lambda _session: version_repo,
        project_paper_repo_factory=lambda _session: relation_repo,
    )

    added = await service.add_existing_paper(
        actor, project.project_id, paper.paper_id, version.version_id
    )
    removed = await service.remove_paper(actor, project.project_id, paper.paper_id)

    assert added.already_added is False
    assert removed is True
    assert await relation_repo.get(project.project_id, paper.paper_id) is None
    assert await paper_repo.get_by_id(paper.paper_id) == paper
    assert await version_repo.get_by_id(version.version_id) == version


@pytest.mark.asyncio
async def test_add_paper_to_archived_project_rejected() -> None:
    """已归档 Project 拒绝收录 Paper。"""
    project_repo = FakeProjectRepository()
    paper_repo = FakePaperRepository()
    version_repo = FakePaperVersionRepository()
    relation_repo = FakeProjectPaperRepository()
    actor = ActorContext(owner_id="user-1")
    project = create_project(actor.owner_id, "归档项目", "").archive()
    paper = create_paper(actor.owner_id)
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id=actor.owner_id,
        file_hash="hash",
        storage_key="object.pdf",
        size_bytes=42,
        content_type="application/pdf",
    )
    await project_repo.add(project)
    await paper_repo.add(paper)
    await version_repo.add(version)
    service = _build_service(project_repo, paper_repo, version_repo, relation_repo)

    with pytest.raises(ProjectArchivedError):
        await service.add_existing_paper(
            actor, project.project_id, paper.paper_id, version.version_id
        )

    assert await relation_repo.get(project.project_id, paper.paper_id) is None


@pytest.mark.asyncio
async def test_add_archived_paper_rejected() -> None:
    """已归档 Paper 拒绝收录到 Project。"""
    project_repo = FakeProjectRepository()
    paper_repo = FakePaperRepository()
    version_repo = FakePaperVersionRepository()
    relation_repo = FakeProjectPaperRepository()
    actor = ActorContext(owner_id="user-1")
    project = create_project(actor.owner_id, "研究项目", "")
    paper = create_paper(actor.owner_id).archive()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id=actor.owner_id,
        file_hash="hash",
        storage_key="object.pdf",
        size_bytes=42,
        content_type="application/pdf",
    )
    await project_repo.add(project)
    await paper_repo.add(paper)
    await version_repo.add(version)
    service = _build_service(project_repo, paper_repo, version_repo, relation_repo)

    with pytest.raises(PaperArchivedError):
        await service.add_existing_paper(
            actor, project.project_id, paper.paper_id, version.version_id
        )

    assert await relation_repo.get(project.project_id, paper.paper_id) is None


@pytest.mark.asyncio
async def test_remove_paper_from_archived_project_rejected() -> None:
    """已归档 Project 拒绝移除收录关系。"""
    project_repo = FakeProjectRepository()
    paper_repo = FakePaperRepository()
    version_repo = FakePaperVersionRepository()
    relation_repo = FakeProjectPaperRepository()
    actor = ActorContext(owner_id="user-1")
    project = create_project(actor.owner_id, "研究项目", "")
    paper = create_paper(actor.owner_id)
    await project_repo.add(project)
    await paper_repo.add(paper)
    service = _build_service(project_repo, paper_repo, version_repo, relation_repo)
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id=actor.owner_id,
        file_hash="hash",
        storage_key="object.pdf",
        size_bytes=42,
        content_type="application/pdf",
    )
    await version_repo.add(version)
    await service.add_existing_paper(actor, project.project_id, paper.paper_id, version.version_id)

    archived_project = project.archive()
    await project_repo.update(archived_project)

    with pytest.raises(ProjectArchivedError):
        await service.remove_paper(actor, project.project_id, paper.paper_id)

    # 关系保持不变：归档只冻结写操作，不破坏历史数据
    assert await relation_repo.get(project.project_id, paper.paper_id) is not None
