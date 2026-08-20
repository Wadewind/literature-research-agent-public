"""Paper PostgreSQL Repository 集成测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.paper import create_paper
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
