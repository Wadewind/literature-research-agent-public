"""ProjectPaper PostgreSQL Repository 集成测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)


@pytest.mark.asyncio
async def test_add_query_and_remove_membership(
    session: AsyncSession,
    project: str,
) -> None:
    """收录关系应固定 Version，删除后 Paper 与 Version 仍存在。"""
    paper_repo = SqlalchemyPaperRepository(session)
    version_repo = SqlalchemyPaperVersionRepository(session)
    relation_repo = SqlalchemyProjectPaperRepository(session)
    paper = create_paper("user-1")
    await paper_repo.add(paper)
    await session.flush()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="d" * 64,
        storage_key="user-1/papers/d.pdf",
        size_bytes=100,
        content_type="application/pdf",
    )
    await version_repo.add(version)
    await session.flush()
    relation = create_project_paper(project, paper.paper_id, version.version_id)
    await relation_repo.add(relation)
    await session.commit()

    assert await relation_repo.get_by_version(project, version.version_id) == relation
    assert await relation_repo.remove(project, paper.paper_id) is True
    await session.commit()
    assert await paper_repo.get_by_id(paper.paper_id) == paper
    assert await version_repo.get_by_id(version.version_id) == version
