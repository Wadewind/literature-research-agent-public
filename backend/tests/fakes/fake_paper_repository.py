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

    async def update(self, paper: Paper) -> None:
        """按主键覆盖内存中的 Paper。"""
        if paper.paper_id in self._papers:
            self._papers[paper.paper_id] = paper

    async def get_by_id(self, paper_id: str) -> Paper | None:
        """根据 ID 返回 Paper。"""
        return self._papers.get(paper_id)

    async def list_by_owner(
        self,
        owner_id: str,
        include_archived: bool = False,
    ) -> list[Paper]:
        """返回指定 owner 的个人文献库；默认排除已归档。"""
        return [
            p
            for p in self._papers.values()
            if p.owner_id == owner_id and (include_archived or not p.is_archived)
        ]
