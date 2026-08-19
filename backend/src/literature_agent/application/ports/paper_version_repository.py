"""PaperVersion Repository 端口。"""

from typing import Protocol

from literature_agent.domain.paper_version import PaperVersion


class PaperVersionRepository(Protocol):
    """PaperVersion 持久化的抽象端口。"""

    async def add(self, version: PaperVersion) -> PaperVersion:
        """保存 PaperVersion。"""
        ...

    async def get_by_id(self, version_id: str) -> PaperVersion | None:
        """按 ID 查询 PaperVersion；不存在返回 None。"""
        ...

    async def list_by_paper(self, paper_id: str) -> list[PaperVersion]:
        """按 Paper ID 列出所有版本。"""
        ...

    async def set_current_parse_revision(self, version_id: str, revision_id: str) -> None:
        """把 Version 的当前 Parse Revision 指针指向指定 Revision。"""
        ...
