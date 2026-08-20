"""Paper 归档/恢复应用服务测试。"""

import pytest

from literature_agent.application.paper_service import PaperService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import PaperNotFoundError
from literature_agent.domain.paper import create_paper
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_project_repository import fake_session


@pytest.fixture
def paper_repo() -> FakePaperRepository:
    """提供 Fake Paper Repository。"""
    return FakePaperRepository()


@pytest.fixture
def service(paper_repo: FakePaperRepository) -> PaperService:
    """提供使用 Fake Repository 的 PaperService。"""
    return PaperService(
        session_factory=fake_session,
        paper_repo_factory=lambda _session: paper_repo,
    )


@pytest.mark.asyncio
async def test_archive_and_restore_paper(
    service: PaperService,
    paper_repo: FakePaperRepository,
) -> None:
    """归档后恢复，状态往返正确。"""
    actor = ActorContext(owner_id="user-1")
    paper = create_paper(actor.owner_id)
    await paper_repo.add(paper)

    archived = await service.archive_paper(actor, paper.paper_id)
    assert archived.is_archived is True
    fetched = await paper_repo.get_by_id(paper.paper_id)
    assert fetched is not None and fetched.is_archived is True

    restored = await service.restore_paper(actor, paper.paper_id)
    assert restored.is_archived is False
    fetched = await paper_repo.get_by_id(paper.paper_id)
    assert fetched is not None and fetched.is_archived is False


@pytest.mark.asyncio
async def test_archive_is_idempotent(
    service: PaperService,
    paper_repo: FakePaperRepository,
) -> None:
    """重复归档返回已归档状态，不刷新时间。"""
    actor = ActorContext(owner_id="user-1")
    paper = create_paper(actor.owner_id)
    await paper_repo.add(paper)

    first = await service.archive_paper(actor, paper.paper_id)
    second = await service.archive_paper(actor, paper.paper_id)

    assert second.is_archived is True
    assert second.archived_at == first.archived_at


@pytest.mark.asyncio
async def test_restore_is_idempotent(
    service: PaperService,
    paper_repo: FakePaperRepository,
) -> None:
    """对 active Paper 恢复是幂等成功。"""
    actor = ActorContext(owner_id="user-1")
    paper = create_paper(actor.owner_id)
    await paper_repo.add(paper)

    restored = await service.restore_paper(actor, paper.paper_id)

    assert restored.is_archived is False


@pytest.mark.asyncio
async def test_archive_not_found_or_forbidden(
    service: PaperService,
    paper_repo: FakePaperRepository,
) -> None:
    """不存在或越权的 Paper 抛 PaperNotFoundError。"""
    actor_a = ActorContext(owner_id="user-a")
    actor_b = ActorContext(owner_id="user-b")
    paper = create_paper(actor_a.owner_id)
    await paper_repo.add(paper)

    with pytest.raises(PaperNotFoundError):
        await service.archive_paper(actor_b, paper.paper_id)
    with pytest.raises(PaperNotFoundError):
        await service.restore_paper(actor_b, paper.paper_id)
    with pytest.raises(PaperNotFoundError):
        await service.archive_paper(actor_a, "missing")
