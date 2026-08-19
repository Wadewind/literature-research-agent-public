"""Parse Revision Repository 端口。"""

from typing import Protocol

from literature_agent.domain.parse_revision import DocumentParseRevision


class ParseRevisionRepository(Protocol):
    """Document Parse Revision 持久化的抽象端口。"""

    async def add(self, revision: DocumentParseRevision) -> DocumentParseRevision:
        """保存 Parse Revision。"""
        ...

    async def get_by_id(self, revision_id: str) -> DocumentParseRevision | None:
        """按 ID 查询；不存在返回 None。"""
        ...

    async def get_by_version_and_profile(
        self,
        version_id: str,
        parser_profile_hash: str,
    ) -> DocumentParseRevision | None:
        """按 Paper Version 和 profile 哈希查询（每个组合至多一条）。"""
        ...

    async def save(self, revision: DocumentParseRevision) -> None:
        """保存 Revision 状态更新（成功/失败收尾）。"""
        ...
