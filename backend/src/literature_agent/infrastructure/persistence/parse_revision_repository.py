"""Parse Revision Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.parse_revision_repository import (
    ParseRevisionRepository,
)
from literature_agent.domain.parse_revision import (
    DocumentParseRevision,
    ParseRevisionStatus,
)
from literature_agent.infrastructure.persistence.models import DocumentParseRevisionORM


def _to_domain(orm: DocumentParseRevisionORM) -> DocumentParseRevision:
    """将 ORM 模型转换为领域实体。"""
    return DocumentParseRevision(
        revision_id=orm.revision_id,
        version_id=orm.version_id,
        parser_name=orm.parser_name,
        parser_version=orm.parser_version,
        parser_profile_hash=orm.parser_profile_hash,
        status=ParseRevisionStatus(orm.status),
        config=orm.config,
        error=orm.error,
        created_at=orm.created_at,
        completed_at=orm.completed_at,
    )


def _to_orm(revision: DocumentParseRevision) -> DocumentParseRevisionORM:
    """将领域实体转换为 ORM 模型。"""
    return DocumentParseRevisionORM(
        revision_id=revision.revision_id,
        version_id=revision.version_id,
        parser_name=revision.parser_name,
        parser_version=revision.parser_version,
        parser_profile_hash=revision.parser_profile_hash,
        status=revision.status.value,
        config=revision.config,
        error=revision.error,
        created_at=revision.created_at,
        completed_at=revision.completed_at,
    )


class SqlalchemyParseRevisionRepository(ParseRevisionRepository):
    """基于 SQLAlchemy AsyncSession 的 ParseRevisionRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, revision: DocumentParseRevision) -> DocumentParseRevision:
        """保存 Parse Revision。"""
        self._session.add(_to_orm(revision))
        return revision

    async def get_by_id(self, revision_id: str) -> DocumentParseRevision | None:
        """按 ID 查询 Parse Revision。"""
        result = await self._session.execute(
            select(DocumentParseRevisionORM).where(
                DocumentParseRevisionORM.revision_id == revision_id
            ),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def get_by_version_and_profile(
        self,
        version_id: str,
        parser_profile_hash: str,
    ) -> DocumentParseRevision | None:
        """按 Paper Version 和 profile 哈希查询。"""
        result = await self._session.execute(
            select(DocumentParseRevisionORM).where(
                DocumentParseRevisionORM.version_id == version_id,
                DocumentParseRevisionORM.parser_profile_hash == parser_profile_hash,
            ),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def save(self, revision: DocumentParseRevision) -> None:
        """保存 Revision 状态更新。"""
        result = await self._session.execute(
            select(DocumentParseRevisionORM).where(
                DocumentParseRevisionORM.revision_id == revision.revision_id
            ),
        )
        orm = result.scalar_one()
        orm.status = revision.status.value
        orm.error = revision.error
        orm.completed_at = revision.completed_at
