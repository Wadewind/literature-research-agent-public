"""Project 对个人文献库 Paper 的收录关系。"""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ProjectPaper:
    """Project 收录 Paper，并固定本 Project 使用的 Version。"""

    project_id: str
    paper_id: str
    selected_version_id: str
    created_at: datetime


def create_project_paper(
    project_id: str,
    paper_id: str,
    selected_version_id: str,
) -> ProjectPaper:
    """创建新的 ProjectPaper 收录关系。"""
    return ProjectPaper(
        project_id=project_id,
        paper_id=paper_id,
        selected_version_id=selected_version_id,
        created_at=datetime.now(UTC),
    )
