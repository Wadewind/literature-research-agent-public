"""ProjectPaper Repository 端口。"""

from typing import Protocol

from literature_agent.domain.project_paper import ProjectPaper


class ProjectPaperRepository(Protocol):
    """Project 收录关系的持久化抽象。"""

    async def add(self, relation: ProjectPaper) -> ProjectPaper:
        """保存收录关系。"""
        ...

    async def get(self, project_id: str, paper_id: str) -> ProjectPaper | None:
        """按 Project 和 Paper 查询关系。"""
        ...

    async def get_by_version(
        self,
        project_id: str,
        version_id: str,
    ) -> ProjectPaper | None:
        """查询 Project 是否收录指定 Version。"""
        ...

    async def list_by_project(self, project_id: str) -> list[ProjectPaper]:
        """列出 Project 的全部收录关系。"""
        ...

    async def list_by_paper(self, paper_id: str) -> list[ProjectPaper]:
        """列出 Paper 被哪些 Project 收录。"""
        ...

    async def remove(self, project_id: str, paper_id: str) -> bool:
        """删除收录关系；存在并删除时返回 true。"""
        ...
