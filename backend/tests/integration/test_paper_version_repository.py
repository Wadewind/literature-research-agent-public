"""PaperVersion PostgreSQL Repository 集成测试。"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)


@pytest.fixture
def paper_repo(session: AsyncSession) -> SqlalchemyPaperRepository:
    """提供 Paper Repository。"""
    return SqlalchemyPaperRepository(session)


@pytest.fixture
def version_repo(session: AsyncSession) -> SqlalchemyPaperVersionRepository:
    """提供 PaperVersion Repository。"""
    return SqlalchemyPaperVersionRepository(session)


@pytest.mark.asyncio
async def test_add_and_get_version(
    paper_repo: SqlalchemyPaperRepository,
    version_repo: SqlalchemyPaperVersionRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """保存并查询 PaperVersion。"""
    paper = create_paper(owner_id="user-1")
    await paper_repo.add(paper)
    await session.flush()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="abc",
        storage_key="user-1/project/paper/paper.pdf",
        size_bytes=1024,
        content_type="application/pdf",
        display_filename="paper.pdf",
    )
    await version_repo.add(version)
    await session.commit()

    fetched = await version_repo.get_by_id(version.version_id)

    assert fetched is not None
    assert fetched.version_id == version.version_id
    assert fetched.file_hash == "abc"
    assert fetched.display_filename == "paper.pdf"


@pytest.mark.asyncio
async def test_owner_file_hash_is_unique(
    paper_repo: SqlalchemyPaperRepository,
    version_repo: SqlalchemyPaperVersionRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """同一 owner 的相同 PDF 哈希不能创建第二个 Version。"""
    first_paper = create_paper(owner_id="user-1")
    second_paper = create_paper(owner_id="user-1")
    await paper_repo.add(first_paper)
    await paper_repo.add(second_paper)
    await session.flush()
    for paper in (first_paper, second_paper):
        await version_repo.add(
            create_paper_version(
                paper_id=paper.paper_id,
                owner_id="user-1",
                file_hash="same-hash",
                storage_key=f"{paper.paper_id}.pdf",
                size_bytes=10,
                content_type="application/pdf",
            )
        )
    with pytest.raises(IntegrityError):
        await session.flush()
