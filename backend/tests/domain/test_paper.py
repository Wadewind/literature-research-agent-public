"""Paper 领域模型测试。"""

from datetime import UTC

import pytest

from literature_agent.domain.paper import PaperTitleSource, create_paper


def test_new_paper_is_active() -> None:
    """新创建的 Paper 默认未归档。"""
    paper = create_paper(owner_id="user-1")

    assert paper.archived_at is None
    assert paper.is_archived is False


def test_archive_sets_archived_at() -> None:
    """归档应写入 archived_at，原实体保持不变。"""
    paper = create_paper(owner_id="user-1")

    archived = paper.archive()

    assert archived.is_archived is True
    assert archived.archived_at is not None
    assert archived.archived_at.tzinfo == UTC
    assert paper.is_archived is False


def test_archive_is_idempotent() -> None:
    """重复归档返回同一实体。"""
    paper = create_paper(owner_id="user-1")
    archived = paper.archive()

    assert archived.archive() is archived


def test_restore_clears_archived_at() -> None:
    """恢复后 archived_at 清空。"""
    paper = create_paper(owner_id="user-1")
    archived = paper.archive()

    restored = archived.restore()

    assert restored.is_archived is False
    assert restored.archived_at is None


def test_restore_active_paper_is_noop() -> None:
    """对未归档 Paper 恢复是幂等空操作。"""
    paper = create_paper(owner_id="user-1")

    assert paper.restore() is paper


def test_create_paper_normalizes_title_and_records_source() -> None:
    """标题进入 Paper 时压平空白，并保留可审计来源。"""
    paper = create_paper(
        owner_id="user-1",
        title="  Learning\n  to   Plan  ",
        title_source=PaperTitleSource.PARSED_DOCUMENT,
    )

    assert paper.title == "Learning to Plan"
    assert paper.title_source is PaperTitleSource.PARSED_DOCUMENT


def test_title_and_source_must_be_present_together() -> None:
    """标题与来源必须同时为空或同时存在。"""
    with pytest.raises(ValueError, match="paper_title_source_required"):
        create_paper(owner_id="user-1", title="A title")

    with pytest.raises(ValueError, match="paper_title_required"):
        create_paper(
            owner_id="user-1",
            title_source=PaperTitleSource.PARSED_DOCUMENT,
        )


def test_arxiv_title_overrides_parsed_title_but_not_the_reverse() -> None:
    """arXiv 元数据优先级高于 PDF 解析标题。"""
    parsed = create_paper(owner_id="user-1").with_title(
        "Parsed title", PaperTitleSource.PARSED_DOCUMENT
    )
    arxiv = parsed.with_title("Authoritative title", PaperTitleSource.ARXIV_METADATA)

    assert arxiv.title == "Authoritative title"
    assert arxiv.title_source is PaperTitleSource.ARXIV_METADATA
    assert (
        arxiv.with_title("Later parsed title", PaperTitleSource.PARSED_DOCUMENT)
        is arxiv
    )
