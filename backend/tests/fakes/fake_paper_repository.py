"""Paper Repository 的内存假实现。"""

from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.domain.paper import Paper


class FakePaperRepository(PaperRepository):
    """不依赖数据库的 Paper Repository 假实现。"""

    def __init__(self) -> None:
        self._papers: dict[str, Paper] = {}

    async def add(self, paper: Paper) -> Paper:
        """将 Paper 存入内存。"""
        self._papers[paper.paper_id] = paper
        return paper

    async def get_by_id(self, paper_id: str) -> Paper | None:
        """根据 ID 返回 Paper。"""
        return self._papers.get(paper_id)

    async def list_by_project(self, project_id: str) -> list[Paper]:
        """返回指定 Project 的 Paper 列表。"""
        return [p for p in self._papers.values() if p.project_id == project_id]
