"""Message Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.message_repository import MessageRepository
from literature_agent.domain.conversation import Message, MessageRole
from literature_agent.infrastructure.persistence.models import MessageORM


def _to_domain(orm: MessageORM) -> Message:
    """将 ORM 模型转换为领域实体。"""
    return Message(
        message_id=orm.message_id,
        conversation_id=orm.conversation_id,
        sequence=orm.sequence,
        role=MessageRole(orm.role),
        content=orm.content,
        run_id=orm.run_id,
        claim_set_id=orm.claim_set_id,
        created_at=orm.created_at,
    )


class SqlalchemyMessageRepository(MessageRepository):
    """基于 SQLAlchemy AsyncSession 的 MessageRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, message: Message) -> Message:
        """保存 Message。"""
        self._session.add(
            MessageORM(
                message_id=message.message_id,
                conversation_id=message.conversation_id,
                sequence=message.sequence,
                role=message.role.value,
                content=message.content,
                run_id=message.run_id,
                claim_set_id=message.claim_set_id,
                created_at=message.created_at,
            )
        )
        return message

    async def get_by_id(self, message_id: str) -> Message | None:
        """按 ID 查询 Message。"""
        result = await self._session.execute(
            select(MessageORM).where(MessageORM.message_id == message_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm is not None else None

    async def list_by_conversation(self, conversation_id: str) -> list[Message]:
        """按会话查询消息，按 sequence 升序返回。"""
        result = await self._session.execute(
            select(MessageORM)
            .where(MessageORM.conversation_id == conversation_id)
            .order_by(MessageORM.sequence),
        )
        return [_to_domain(row) for row in result.scalars().all()]

    async def get_by_run_and_role(
        self,
        run_id: str,
        role: MessageRole,
    ) -> Message | None:
        """按关联 Run 与角色查询消息。"""
        result = await self._session.execute(
            select(MessageORM).where(
                MessageORM.run_id == run_id,
                MessageORM.role == role.value,
            ),
        )
        orm = result.scalars().first()
        return _to_domain(orm) if orm is not None else None

    async def max_sequence(self, conversation_id: str) -> int:
        """返回会话内当前最大 sequence；无消息返回 0。"""
        result = await self._session.execute(
            select(func.coalesce(func.max(MessageORM.sequence), 0)).where(
                MessageORM.conversation_id == conversation_id
            ),
        )
        return result.scalar_one()
