"""Project 领域实体与工厂。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

MAX_NAME_LENGTH = 200


@dataclass(frozen=True, slots=True)
class Project:
    """Research Project 领域实体。

    Project 是用户组织一次研究主题的顶层资源，所有查询必须在
    ``owner_id`` 范围内执行。

    属性:
        project_id: 稳定的项目标识符。
        owner_id: 项目所有者的标识符。
        name: 项目名称。
        description: 项目说明。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    project_id: str
    owner_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


def create_project(owner_id: str, name: str, description: str) -> Project:
    """创建新的 Project 实体。

    参数:
        owner_id: 项目所有者标识符。
        name: 项目名称，不能为空且不能超过 ``MAX_NAME_LENGTH``。
        description: 项目说明。

    返回:
        新创建的 ``Project`` 实例。

    异常:
        ValueError: 当 ``name`` 为空或长度超过上限时抛出。
    """
    if not name:
        raise ValueError("项目名称不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"项目名称长度不能超过 {MAX_NAME_LENGTH}")

    now = datetime.now(UTC)
    return Project(
        project_id=str(uuid4()),
        owner_id=owner_id,
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
    )
