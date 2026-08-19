"""Parse Revision Repository 的内存假实现。"""

from literature_agent.application.ports.parse_revision_repository import (
    ParseRevisionRepository,
)
from literature_agent.domain.parse_revision import DocumentParseRevision


class FakeParseRevisionRepository(ParseRevisionRepository):
    """不依赖数据库的 Parse Revision Repository 假实现。"""

    def __init__(self) -> None:
        self._revisions: dict[str, DocumentParseRevision] = {}

    async def add(self, revision: DocumentParseRevision) -> DocumentParseRevision:
        """将 Revision 存入内存。"""
        self._revisions[revision.revision_id] = revision
        return revision

    async def get_by_id(self, revision_id: str) -> DocumentParseRevision | None:
        """按 ID 返回 Revision。"""
        return self._revisions.get(revision_id)

    async def get_by_version_and_profile(
        self,
        version_id: str,
        parser_profile_hash: str,
    ) -> DocumentParseRevision | None:
        """按 Version 和 profile 哈希返回 Revision。"""
        for revision in self._revisions.values():
            if (
                revision.version_id == version_id
                and revision.parser_profile_hash == parser_profile_hash
            ):
                return revision
        return None

    async def save(self, revision: DocumentParseRevision) -> None:
        """保存 Revision 状态更新。"""
        self._revisions[revision.revision_id] = revision
