"""Paper 领域实体。"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Paper:
    """学术作品在系统中的稳定身份。

    属性:
        paper_id: 稳定的 Paper 标识符。
        owner_id: 所有者标识符。
        created_at: 创建时间（UTC）。
        archived_at: 归档时间（UTC），None 表示 active。
    """

    paper_id: str
    owner_id: str
    created_at: datetime
    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        """是否已归档。"""
        return self.archived_at is not None

    def archive(self) -> "Paper":
        """归档 Paper；已归档时幂等返回自身。"""
        if self.is_archived:
            return self
        return replace(self, archived_at=datetime.now(UTC))

    def restore(self) -> "Paper":
        """恢复已归档 Paper；未归档时幂等返回自身。"""
        if not self.is_archived:
            return self
        return replace(self, archived_at=None)


def create_paper(owner_id: str) -> Paper:
    """创建新的 Paper 实体。

    参数:
        owner_id: 所有者标识符。
    返回:
        新创建的 Paper。
    """
    now = datetime.now(UTC)
    return Paper(
        paper_id=str(uuid4()),
        owner_id=owner_id,
        created_at=now,
    )
