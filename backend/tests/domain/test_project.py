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
