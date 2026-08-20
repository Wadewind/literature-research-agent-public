"""ProjectPaper Repository 的内存假实现。"""

from literature_agent.application.ports.project_paper_repository import (
    ProjectPaperRepository,
)
from literature_agent.domain.project_paper import ProjectPaper


class FakeProjectPaperRepository(ProjectPaperRepository):
    """用于应用测试的收录关系内存仓库。"""

    def __init__(self) -> None:
        self._relations: dict[tuple[str, str], ProjectPaper] = {}

    async def add(self, relation: ProjectPaper) -> ProjectPaper:
        """保存关系；同一 Project/Paper 幂等覆盖。"""
        self._relations[(relation.project_id, relation.paper_id)] = relation
        return relation

    async def get(self, project_id: str, paper_id: str) -> ProjectPaper | None:
        """查询关系。"""
        return self._relations.get((project_id, paper_id))

    async def get_by_version(
        self,
        project_id: str,
        version_id: str,
    ) -> ProjectPaper | None:
        """按 Project 和 Version 查询关系。"""
        return next(
            (
                relation
                for relation in self._relations.values()
                if relation.project_id == project_id
                and relation.selected_version_id == version_id
            ),
            None,
        )

    async def list_by_project(self, project_id: str) -> list[ProjectPaper]:
        """列出 Project 的关系。"""
        return [r for r in self._relations.values() if r.project_id == project_id]

    async def list_by_paper(self, paper_id: str) -> list[ProjectPaper]:
        """列出 Paper 的关系。"""
        return [r for r in self._relations.values() if r.paper_id == paper_id]

    async def remove(self, project_id: str, paper_id: str) -> bool:
        """删除关系。"""
        return self._relations.pop((project_id, paper_id), None) is not None
