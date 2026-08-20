"""ProjectPaper 收录关系的领域测试。"""

from literature_agent.domain.project_paper import create_project_paper


def test_project_paper_pins_selected_version() -> None:
    """Project 收录 Paper 时必须固定一个 Version。"""
    relation = create_project_paper(
        project_id="project-1",
        paper_id="paper-1",
        selected_version_id="version-1",
    )

    assert relation.project_id == "project-1"
    assert relation.paper_id == "paper-1"
    assert relation.selected_version_id == "version-1"
