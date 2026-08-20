"""个人文献库与 Project 收录服务测试。"""

import pytest

from literature_agent.application.project_library_service import ProjectLibraryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.project import create_project
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session


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
