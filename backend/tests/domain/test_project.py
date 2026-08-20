"""Project 领域模型测试。"""

from datetime import UTC, datetime

import pytest

from literature_agent.domain.project import create_project


def test_create_project_has_required_fields() -> None:
    """create_project 应创建包含必要字段的 Project。"""
    project = create_project(owner_id="user-1", name="综述项目", description="测试描述")

    assert project.owner_id == "user-1"
    assert project.name == "综述项目"
    assert project.description == "测试描述"
    assert project.project_id
    assert isinstance(project.created_at, datetime)
    assert isinstance(project.updated_at, datetime)
    assert project.created_at.tzinfo == UTC


def test_create_project_rejects_empty_name() -> None:
    """项目名称不能为空。"""
    with pytest.raises(ValueError, match="名称"):
        create_project(owner_id="user-1", name="", description="描述")


def test_create_project_rejects_too_long_name() -> None:
    """项目名称超过最大长度应报错。"""
    with pytest.raises(ValueError, match="长度"):
        create_project(owner_id="user-1", name="x" * 201, description="描述")


def test_project_is_frozen() -> None:
    """Project 实体不可变。"""
    project = create_project(owner_id="user-1", name="项目", description="")

    with pytest.raises(AttributeError):
        project.name = "新名称"


def test_new_project_is_active() -> None:
    """新创建的 Project 默认未归档。"""
    project = create_project(owner_id="user-1", name="项目", description="")

    assert project.archived_at is None
    assert project.is_archived is False


def test_archive_sets_archived_at_and_updates_timestamp() -> None:
    """归档应写入 archived_at 并刷新 updated_at。"""
    project = create_project(owner_id="user-1", name="项目", description="")

    archived = project.archive()

    assert archived.is_archived is True
    assert archived.archived_at is not None
    assert archived.archived_at.tzinfo == UTC
    assert archived.updated_at >= project.updated_at
    # 原实体不可变
    assert project.is_archived is False


def test_archive_is_idempotent() -> None:
    """重复归档返回同一实体，不刷新时间戳。"""
    project = create_project(owner_id="user-1", name="项目", description="")
    archived = project.archive()

    assert archived.archive() is archived


def test_restore_clears_archived_at() -> None:
    """恢复归档 Project 后 archived_at 清空并刷新 updated_at。"""
    project = create_project(owner_id="user-1", name="项目", description="")
    archived = project.archive()

    restored = archived.restore()

    assert restored.is_archived is False
    assert restored.archived_at is None
    assert restored.updated_at >= archived.updated_at


def test_restore_active_project_is_noop() -> None:
    """对未归档 Project 恢复是幂等空操作。"""
    project = create_project(owner_id="user-1", name="项目", description="")

    assert project.restore() is project


def test_update_details_changes_name_and_description() -> None:
    """update_details 同时修改名称与说明并刷新 updated_at。"""
    project = create_project(owner_id="user-1", name="旧名称", description="旧说明")

    updated = project.update_details(name="新名称", description="新说明")

    assert updated.name == "新名称"
    assert updated.description == "新说明"
    assert updated.updated_at >= project.updated_at
    assert project.name == "旧名称"


def test_update_details_allows_partial_update() -> None:
    """只传一个字段时另一字段保持不变。"""
    project = create_project(owner_id="user-1", name="旧名称", description="旧说明")

    renamed = project.update_details(name="新名称")
    redescribed = project.update_details(description="新说明")

    assert renamed.name == "新名称"
    assert renamed.description == "旧说明"
    assert redescribed.name == "旧名称"
    assert redescribed.description == "新说明"


def test_update_details_requires_at_least_one_field() -> None:
    """两个字段都缺失时应报错。"""
    project = create_project(owner_id="user-1", name="项目", description="")

    with pytest.raises(ValueError, match="至少"):
        project.update_details()


def test_update_details_rejects_invalid_name() -> None:
    """修改名称沿用创建时的校验规则。"""
    project = create_project(owner_id="user-1", name="项目", description="")

    with pytest.raises(ValueError, match="名称"):
        project.update_details(name="")
    with pytest.raises(ValueError, match="长度"):
        project.update_details(name="x" * 201)
