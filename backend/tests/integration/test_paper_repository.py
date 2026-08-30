"""Paper PostgreSQL Repository 集成测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.paper import PaperTitleSource, create_paper
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)


@pytest.fixture
def repo(session: AsyncSession) -> SqlalchemyPaperRepository:
    """提供基于真实会话的 Repository。"""
    return SqlalchemyPaperRepository(session)


@pytest.mark.asyncio
async def test_add_and_get_paper(
    repo: SqlalchemyPaperRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """保存并查询 Paper。"""
    paper = create_paper(owner_id="user-1")
    await repo.add(paper)
    await session.commit()

    fetched = await repo.get_by_id(paper.paper_id)

    assert fetched is not None
    assert fetched.paper_id == paper.paper_id
    assert fetched.owner_id == "user-1"


@pytest.mark.asyncio
async def test_list_by_owner_isolation(
    repo: SqlalchemyPaperRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """个人文献库按 owner 隔离。"""
    paper_a = create_paper(owner_id="user-1")
    paper_b = create_paper(owner_id="user-2")
    await repo.add(paper_a)
    await repo.add(paper_b)
    await session.commit()

    results = await repo.list_by_owner("user-1")

    assert len(results) == 1
    assert results[0].owner_id == "user-1"


@pytest.mark.asyncio
async def test_update_persists_archived_at(
    repo: SqlalchemyPaperRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """update 应持久化归档时间与恢复。"""
    paper = create_paper(owner_id="user-1")
    await repo.add(paper)
    await session.commit()

    await repo.update(paper.archive())
    await session.commit()

    fetched = await repo.get_by_id(paper.paper_id)
    assert fetched is not None
    assert fetched.is_archived is True
    assert fetched.archived_at is not None

    await repo.update(fetched.restore())
    await session.commit()
    restored = await repo.get_by_id(paper.paper_id)
    assert restored is not None
    assert restored.is_archived is False


@pytest.mark.asyncio
async def test_title_and_source_round_trip(
    repo: SqlalchemyPaperRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """标题及来源可新增、更新并从 PostgreSQL 还原为领域枚举。"""
    paper = create_paper(owner_id="user-1")
    await repo.add(paper)
    await session.commit()

    await repo.update(
        paper.with_title("A Parsed Paper Title", PaperTitleSource.PARSED_DOCUMENT)
    )
    await session.commit()

    fetched = await repo.get_by_id(paper.paper_id)
    assert fetched is not None
    assert fetched.title == "A Parsed Paper Title"
    assert fetched.title_source is PaperTitleSource.PARSED_DOCUMENT


@pytest.mark.asyncio
async def test_list_by_owner_filters_archived(
    repo: SqlalchemyPaperRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """个人文献库默认排除已归档 Paper，include_archived 时返回全部。"""
    active = create_paper(owner_id="user-1")
    archived = create_paper(owner_id="user-1").archive()
    await repo.add(active)
    await repo.add(archived)
    await session.commit()

    default = await repo.list_by_owner("user-1")
    full = await repo.list_by_owner("user-1", include_archived=True)

    assert [p.paper_id for p in default] == [active.paper_id]
    assert len(full) == 2
