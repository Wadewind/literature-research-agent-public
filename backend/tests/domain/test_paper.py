"""Paper 领域模型测试。"""

from datetime import UTC

from literature_agent.domain.paper import create_paper


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
