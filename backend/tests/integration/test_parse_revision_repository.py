"""Parse Revision / Element Repository 的 PostgreSQL 集成测试。"""

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
    ElementType,
    compute_content_hash,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import (
    ParseRevisionStatus,
    create_parse_revision,
)
from literature_agent.infrastructure.persistence.element_repository import (
    SqlalchemyElementRepository,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.parse_revision_repository import (
    SqlalchemyParseRevisionRepository,
)

_PROFILE = ParseProfile("fake", "1.0", {})


@pytest_asyncio.fixture
async def version_id(session, project: str) -> str:
    """创建 Paper 和 PaperVersion，返回 version_id。"""
    paper = create_paper(owner_id="user-1")
    await SqlalchemyPaperRepository(session).add(paper)
    await session.flush()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="a" * 64,
        storage_key="user-1/proj/paper/paper.pdf",
        size_bytes=100,
        content_type="application/pdf",
    )
    await SqlalchemyPaperVersionRepository(session).add(version)
    await session.commit()
    return version.version_id


@pytest_asyncio.fixture
async def revision_id(session, version_id: str) -> str:
    """创建一个 RUNNING 的 Parse Revision，返回 revision_id。"""
    revision = create_parse_revision(
        version_id, _PROFILE.parser_name, _PROFILE.parser_version, _PROFILE.profile_hash
    )
    await SqlalchemyParseRevisionRepository(session).add(revision)
    await session.commit()
    return revision.revision_id


def _element(revision_id: str, sequence: int, **kwargs) -> DocumentElement:
    """构造测试 Element。"""
    from uuid import uuid4

    text = kwargs.pop("text", f"段落{sequence}")
    return DocumentElement(
        element_id=str(uuid4()),
        revision_id=revision_id,
        element_type=kwargs.pop("element_type", ElementType.PARAGRAPH),
        sequence=sequence,
        text=text,
        content_hash=compute_content_hash("paragraph", text, {}),
        **kwargs,
    )


async def test_revision_add_get_and_unique(session, version_id: str) -> None:
    """Revision 可写入读回；同 version + profile 唯一。"""
    repo = SqlalchemyParseRevisionRepository(session)
    revision = create_parse_revision(
        version_id, _PROFILE.parser_name, _PROFILE.parser_version, _PROFILE.profile_hash
    )
    await repo.add(revision)
    await session.commit()

    loaded = await repo.get_by_version_and_profile(version_id, _PROFILE.profile_hash)
    assert loaded is not None
    assert loaded.revision_id == revision.revision_id
    assert loaded.status == ParseRevisionStatus.RUNNING

    # 唯一约束：同 version + profile 不允许第二条
    duplicate = create_parse_revision(
        version_id, _PROFILE.parser_name, _PROFILE.parser_version, _PROFILE.profile_hash
    )
    await repo.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_revision_save_status(session, revision_id: str) -> None:
    """save 应持久化状态与完成时间。"""
    from datetime import UTC, datetime

    repo = SqlalchemyParseRevisionRepository(session)
    revision = await repo.get_by_id(revision_id)
    assert revision is not None
    now = datetime.now(UTC)
    await repo.save(revision.mark_succeeded(now))
    await session.commit()

    loaded = await repo.get_by_id(revision_id)
    assert loaded is not None
    assert loaded.status == ParseRevisionStatus.SUCCEEDED
    assert loaded.completed_at is not None


async def test_set_current_parse_revision(session, version_id: str, revision_id: str) -> None:
    """当前 Revision 指针应可设置并读回。"""
    repo = SqlalchemyPaperVersionRepository(session)
    await repo.set_current_parse_revision(version_id, revision_id)
    await session.commit()

    version = await repo.get_by_id(version_id)
    assert version is not None
    assert version.current_parse_revision_id == revision_id


async def test_elements_crud_and_sequence_unique(session, revision_id: str) -> None:
    """Element 批量写入、顺序读取与 (revision, sequence) 唯一约束。"""
    repo = SqlalchemyElementRepository(session)
    elements = [_element(revision_id, 1), _element(revision_id, 2)]
    await repo.add_many(elements)
    await repo.add_locations(
        [
            ElementSourceLocation(
                location_id="loc-1", element_id=elements[0].element_id, page=1
            ),
            ElementSourceLocation(
                location_id="loc-2", element_id=elements[1].element_id, page=2, parser_ref="fake:p2"
            ),
        ]
    )
    await session.commit()

    loaded = await repo.list_by_revision(revision_id)
    assert [e.sequence for e in loaded] == [1, 2]
    locations = await repo.list_locations([e.element_id for e in loaded])
    assert len(locations) == 2

    # 唯一约束
    await repo.add_many([_element(revision_id, 1)])
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_list_by_revision_filters(session, revision_id: str) -> None:
    """页码、章节前缀、类型过滤与分页。"""
    repo = SqlalchemyElementRepository(session)
    elements = [
        _element(revision_id, 1, element_type=ElementType.SECTION_HEADING, text="1 引言",
                 section_path="1"),
        _element(revision_id, 2, section_path="1"),
        _element(revision_id, 3, element_type=ElementType.TABLE, text=None, section_path="2"),
    ]
    await repo.add_many(elements)
    await repo.add_locations(
        [
            ElementSourceLocation(location_id="l1", element_id=elements[0].element_id, page=1),
            ElementSourceLocation(location_id="l2", element_id=elements[1].element_id, page=1),
            ElementSourceLocation(location_id="l3", element_id=elements[2].element_id, page=2),
        ]
    )
    await session.commit()

    # 页码过滤
    page1 = await repo.list_by_revision(revision_id, page=1)
    assert [e.sequence for e in page1] == [1, 2]
    # 章节前缀过滤
    sec1 = await repo.list_by_revision(revision_id, section_prefix="1")
    assert [e.sequence for e in sec1] == [1, 2]
    # 类型过滤
    tables = await repo.list_by_revision(revision_id, element_type="table")
    assert [e.sequence for e in tables] == [3]
    # 分页
    page_slice = await repo.list_by_revision(revision_id, limit=1, offset=1)
    assert [e.sequence for e in page_slice] == [2]
