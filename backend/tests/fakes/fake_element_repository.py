"""Element Repository 的内存假实现。"""

from literature_agent.application.ports.element_repository import ElementRepository
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
)


class FakeElementRepository(ElementRepository):
    """不依赖数据库的 Element Repository 假实现。"""

    def __init__(self) -> None:
        self._elements: dict[str, DocumentElement] = {}
        self._locations: list[ElementSourceLocation] = []

    async def add_many(self, elements: list[DocumentElement]) -> None:
        """将 Element 批量存入内存。"""
        for element in elements:
            self._elements[element.element_id] = element

    async def add_locations(self, locations: list[ElementSourceLocation]) -> None:
        """将来源定位批量存入内存。"""
        self._locations.extend(locations)

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
        """按 Revision 返回 Element，支持过滤与分页。"""
        result = [e for e in self._elements.values() if e.revision_id == revision_id]
        if element_type is not None:
            result = [e for e in result if e.element_type.value == element_type]
        if section_prefix is not None:
            result = [
                e for e in result if e.section_path and e.section_path.startswith(section_prefix)
            ]
        if page is not None:
            on_page = {loc.element_id for loc in self._locations if loc.page == page}
            result = [e for e in result if e.element_id in on_page]
        result.sort(key=lambda e: e.sequence)
        return result[offset : offset + limit]

    async def list_locations(self, element_ids: list[str]) -> list[ElementSourceLocation]:
        """按 Element ID 列表返回来源定位。"""
        return [loc for loc in self._locations if loc.element_id in element_ids]
