"""Document Element Repository 端口。"""

from typing import Protocol

from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
)


class ElementRepository(Protocol):
    """Document Element 与来源定位持久化的抽象端口。"""

    async def add_many(self, elements: list[DocumentElement]) -> None:
        """批量保存 Element。"""
        ...

    async def add_locations(self, locations: list[ElementSourceLocation]) -> None:
        """批量保存来源定位。"""
        ...

    async def list_by_revision(
        self,
        revision_id: str,
        *,
        page: int | None = None,
        section_prefix: str | None = None,
        element_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentElement]:
        """按 Revision 查询 Element，支持页码/章节前缀/类型过滤与分页。

        按阅读顺序 ``sequence`` 升序返回。``page`` 过滤命中任一来源定位
        在该页的 Element。
        """
        ...

    async def list_locations(self, element_ids: list[str]) -> list[ElementSourceLocation]:
        """按 Element ID 列表查询来源定位。"""
        ...

    async def count_by_revision(self, revision_id: str) -> int:
        """统计 Revision 下的 Element 数量。"""
        ...
