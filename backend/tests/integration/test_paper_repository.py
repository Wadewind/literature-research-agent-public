"""Paper PostgreSQL Repository 集成测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.paper import create_paper
from literature_agent.domain.project import create_project as create_project_entity
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
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
    paper = create_paper(owner_id="user-1", project_id=project)
    await repo.add(paper)
    await session.commit()

    fetched = await repo.get_by_id(paper.paper_id)

    assert fetched is not None
    assert fetched.paper_id == paper.paper_id
    assert fetched.owner_id == "user-1"


@pytest.mark.asyncio
async def test_list_by_project_isolation(
    repo: SqlalchemyPaperRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """Paper 列表按 Project 隔离。"""
    project_repo = SqlalchemyProjectRepository(session)
    other_project = create_project_entity(owner_id="user-1", name="其他项目", description="")
    await project_repo.add(other_project)
    await session.flush()

    paper_a = create_paper(owner_id="user-1", project_id=project)
    paper_b = create_paper(owner_id="user-1", project_id=other_project.project_id)
    await repo.add(paper_a)
    await repo.add(paper_b)
    await session.commit()

    results = await repo.list_by_project(project)

    assert len(results) == 1
    assert results[0].project_id == project
