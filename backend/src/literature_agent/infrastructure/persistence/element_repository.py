"""Document Element Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.element_repository import ElementRepository
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
    ElementType,
)
from literature_agent.infrastructure.persistence.models import (
    DocumentElementORM,
    ElementSourceLocationORM,
)


def _element_to_domain(orm: DocumentElementORM) -> DocumentElement:
    """将 ORM 模型转换为领域实体。"""
    return DocumentElement(
        element_id=orm.element_id,
        revision_id=orm.revision_id,
        element_type=ElementType(orm.element_type),
        sequence=orm.sequence,
        parent_element_id=orm.parent_element_id,
        section_path=orm.section_path,
        text=orm.text,
        payload=orm.payload,
        content_hash=orm.content_hash,
        warnings=list(orm.warnings),
    )


def _element_to_orm(element: DocumentElement) -> DocumentElementORM:
    """将领域实体转换为 ORM 模型。"""
    return DocumentElementORM(
        element_id=element.element_id,
        revision_id=element.revision_id,
        element_type=element.element_type.value,
        sequence=element.sequence,
        parent_element_id=element.parent_element_id,
        section_path=element.section_path,
        text=element.text,
        payload=element.payload,
        content_hash=element.content_hash,
        warnings=list(element.warnings),
    )


def _location_to_domain(orm: ElementSourceLocationORM) -> ElementSourceLocation:
    """将 ORM 模型转换为领域实体。"""
    return ElementSourceLocation(
        location_id=orm.location_id,
        element_id=orm.element_id,
        page=orm.page,
        bbox=orm.bbox,
        parser_ref=orm.parser_ref,
        char_range=orm.char_range,
    )


def _location_to_orm(location: ElementSourceLocation) -> ElementSourceLocationORM:
    """将领域实体转换为 ORM 模型。"""
    return ElementSourceLocationORM(
        location_id=location.location_id,
        element_id=location.element_id,
        page=location.page,
        bbox=location.bbox,
        parser_ref=location.parser_ref,
        char_range=location.char_range,
    )


class SqlalchemyElementRepository(ElementRepository):
    """基于 SQLAlchemy AsyncSession 的 ElementRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add_many(self, elements: list[DocumentElement]) -> None:
        """批量保存 Element。"""
        self._session.add_all([_element_to_orm(e) for e in elements])

    async def add_locations(self, locations: list[ElementSourceLocation]) -> None:
        """批量保存来源定位。"""
        self._session.add_all([_location_to_orm(loc) for loc in locations])

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
        """按 Revision 查询 Element，支持过滤与分页。"""
        stmt = select(DocumentElementORM).where(
            DocumentElementORM.revision_id == revision_id
        )
        if element_type is not None:
            stmt = stmt.where(DocumentElementORM.element_type == element_type)
        if section_prefix is not None:
            stmt = stmt.where(DocumentElementORM.section_path.startswith(section_prefix))
        if page is not None:
            # 命中任一来源定位在该页的 Element
            stmt = stmt.where(
                DocumentElementORM.element_id.in_(
                    select(ElementSourceLocationORM.element_id).where(
                        ElementSourceLocationORM.page == page
                    )
                )
            )
        stmt = stmt.order_by(DocumentElementORM.sequence).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_element_to_domain(row) for row in result.scalars().all()]

    async def list_locations(self, element_ids: list[str]) -> list[ElementSourceLocation]:
        """按 Element ID 列表查询来源定位。"""
        if not element_ids:
            return []
        result = await self._session.execute(
            select(ElementSourceLocationORM).where(
                ElementSourceLocationORM.element_id.in_(element_ids)
            ),
        )
        return [_location_to_domain(row) for row in result.scalars().all()]

    async def count_by_revision(self, revision_id: str) -> int:
        """统计 Revision 下的 Element 数量。"""
        result = await self._session.execute(
            select(func.count())
            .select_from(DocumentElementORM)
            .where(DocumentElementORM.revision_id == revision_id),
        )
        return result.scalar_one()
