"""PaperVersion Repository 的内存假实现。"""

from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.domain.paper_version import PaperVersion


class FakePaperVersionRepository(PaperVersionRepository):
    """不依赖数据库的 PaperVersion Repository 假实现。"""

    def __init__(self) -> None:
        self._versions: dict[str, PaperVersion] = {}

    async def add(self, version: PaperVersion) -> PaperVersion:
        """将 PaperVersion 存入内存。"""
        self._versions[version.version_id] = version
        return version

    async def get_by_id(self, version_id: str) -> PaperVersion | None:
        """根据 ID 返回 PaperVersion。"""
        return self._versions.get(version_id)

    async def list_by_paper(self, paper_id: str) -> list[PaperVersion]:
        """返回指定 Paper 的版本列表。"""
        return [v for v in self._versions.values() if v.paper_id == paper_id]

    async def set_current_parse_revision(self, version_id: str, revision_id: str) -> None:
        """更新 Version 的当前 Parse Revision 指针。"""
        from dataclasses import replace

        version = self._versions[version_id]
        self._versions[version_id] = replace(
            version, current_parse_revision_id=revision_id
        )
