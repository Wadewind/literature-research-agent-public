"""Paper 领域实体。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Paper:
    """学术作品在系统中的稳定身份。

    属性:
        paper_id: 稳定的 Paper 标识符。
        owner_id: 所有者标识符。
        project_id: 所属 Project 标识符。
        created_at: 创建时间（UTC）。
    """

    paper_id: str
    owner_id: str
    project_id: str
    created_at: datetime


def create_paper(owner_id: str, project_id: str) -> Paper:
    """创建新的 Paper 实体。

    参数:
        owner_id: 所有者标识符。
        project_id: 所属 Project 标识符。

    返回:
        新创建的 Paper。
    """
    now = datetime.now(UTC)
    return Paper(
        paper_id=str(uuid4()),
        owner_id=owner_id,
        project_id=project_id,
        created_at=now,
    )
