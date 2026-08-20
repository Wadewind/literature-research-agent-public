"""Project 领域实体与工厂。"""

from dataclasses import dataclass, replace
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
        archived_at: 归档时间（UTC），None 表示 active。
    """

    project_id: str
    owner_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        """是否已归档。"""
        return self.archived_at is not None

    def archive(self) -> "Project":
        """归档 Project；已归档时幂等返回自身。"""
        if self.is_archived:
            return self
        now = datetime.now(UTC)
        return replace(self, archived_at=now, updated_at=now)

    def restore(self) -> "Project":
        """恢复已归档 Project；未归档时幂等返回自身。"""
        if not self.is_archived:
            return self
        return replace(self, archived_at=None, updated_at=datetime.now(UTC))

    def update_details(
        self,
        name: str | None = None,
        description: str | None = None,
    ) -> "Project":
        """修改名称与说明，至少提供一个字段。

        异常:
            ValueError: 两个字段都缺失，或名称违反校验规则。
        """
        if name is None and description is None:
            raise ValueError("至少需要提供一个待修改字段")
        if name is not None:
            _validate_name(name)
        return replace(
            self,
            name=self.name if name is None else name,
            description=self.description if description is None else description,
            updated_at=datetime.now(UTC),
        )


def _validate_name(name: str) -> None:
    """校验项目名称：非空且不超过 ``MAX_NAME_LENGTH``。"""
    if not name:
        raise ValueError("项目名称不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"项目名称长度不能超过 {MAX_NAME_LENGTH}")


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
    _validate_name(name)

    now = datetime.now(UTC)
    return Project(
        project_id=str(uuid4()),
        owner_id=owner_id,
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
    )
