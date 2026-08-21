"""Conversation Repository 的 PostgreSQL 适配器。"""

from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.conversation_repository import (
    ConversationRepository,
)
from literature_agent.domain.conversation import (
    Conversation,
    ConversationScopePaper,
    ScopeMode,
)
from literature_agent.infrastructure.persistence.models import (
    ConversationORM,
    ConversationScopePaperORM,
)


def _to_domain(orm: ConversationORM) -> Conversation:
    """将 ORM 模型转换为领域实体。"""
    return Conversation(
        conversation_id=orm.conversation_id,
        project_id=orm.project_id,
        owner_id=orm.owner_id,
        title=orm.title,
        scope_mode=ScopeMode(orm.scope_mode),
        active_run_id=orm.active_run_id,
        created_at=orm.created_at,
    )


class SqlalchemyConversationRepository(ConversationRepository):
    """基于 SQLAlchemy AsyncSession 的 ConversationRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, conversation: Conversation) -> Conversation:
        """保存 Conversation。"""
        self._session.add(
            ConversationORM(
                conversation_id=conversation.conversation_id,
                project_id=conversation.project_id,
                owner_id=conversation.owner_id,
                title=conversation.title,
                scope_mode=conversation.scope_mode.value,
                active_run_id=conversation.active_run_id,
                created_at=conversation.created_at,
            )
        )
        return conversation

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        """按 ID 查询 Conversation。"""
        result = await self._session.execute(
            select(ConversationORM).where(
                ConversationORM.conversation_id == conversation_id
            ),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm is not None else None

    async def list_by_project(self, project_id: str) -> list[Conversation]:
        """列出 Project 的会话，按创建时间升序。"""
        result = await self._session.execute(
            select(ConversationORM)
            .where(ConversationORM.project_id == project_id)
            .order_by(ConversationORM.created_at),
        )
        return [_to_domain(row) for row in result.scalars().all()]

    async def add_scope_papers(
        self,
        scope_papers: list[ConversationScopePaper],
    ) -> None:
        """批量保存固化的默认范围条目。"""
        self._session.add_all(
            [
                ConversationScopePaperORM(
                    conversation_id=entry.conversation_id,
                    paper_id=entry.paper_id,
                    version_id=entry.version_id,
                )
                for entry in scope_papers
            ]
        )

    async def list_scope_papers(
        self,
        conversation_id: str,
    ) -> list[ConversationScopePaper]:
        """列出会话固化的默认范围条目。"""
        result = await self._session.execute(
            select(ConversationScopePaperORM).where(
                ConversationScopePaperORM.conversation_id == conversation_id
            ),
        )
        return [
            ConversationScopePaper(
                conversation_id=row.conversation_id,
                paper_id=row.paper_id,
                version_id=row.version_id,
            )
            for row in result.scalars().all()
        ]

    async def try_claim_active_run(self, conversation_id: str, run_id: str) -> bool:
        """条件更新认领活跃 Run：仅当前无活跃 Run 时生效。"""
        result = cast(
            CursorResult,
            await self._session.execute(
                update(ConversationORM)
                .where(
                    ConversationORM.conversation_id == conversation_id,
                    ConversationORM.active_run_id.is_(None),
                )
                .values(active_run_id=run_id),
            ),
        )
        return result.rowcount > 0

    async def release_active_run(
        self,
        conversation_id: str,
        *,
        expected_run_id: str | None = None,
    ) -> bool:
        """清理活跃 Run 认领；可按期望认领值条件清理。"""
        statement = update(ConversationORM).where(
            ConversationORM.conversation_id == conversation_id,
            ConversationORM.active_run_id.is_not(None),
        )
        if expected_run_id is not None:
            statement = statement.where(
                ConversationORM.active_run_id == expected_run_id
            )
        result = cast(
            CursorResult,
            await self._session.execute(statement.values(active_run_id=None)),
        )
        return result.rowcount > 0

    async def set_title_if_null(self, conversation_id: str, title: str) -> None:
        """仅当标题为 null 时回填。"""
        await self._session.execute(
            update(ConversationORM)
            .where(
                ConversationORM.conversation_id == conversation_id,
                ConversationORM.title.is_(None),
            )
            .values(title=title),
        )
