"""Message Repository 端口。"""

from typing import Protocol

from literature_agent.domain.conversation import Message, MessageRole


class MessageRepository(Protocol):
    """Message 持久化的抽象端口。"""

    async def add(self, message: Message) -> Message:
        """保存 Message（唯一约束 ``(conversation_id, sequence)`` 兜底）。"""
        ...

    async def get_by_id(self, message_id: str) -> Message | None:
        """按 ID 查询 Message；不存在返回 None。"""
        ...

    async def list_by_conversation(self, conversation_id: str) -> list[Message]:
        """按会话查询消息，按 ``sequence`` 升序返回。"""
        ...

    async def get_by_run_and_role(
        self,
        run_id: str,
        role: MessageRole,
    ) -> Message | None:
        """按关联 Run 与角色查询消息（幂等重放与幂等完成回读用）。"""
        ...

    async def max_sequence(self, conversation_id: str) -> int:
        """返回会话内当前最大 sequence；无消息返回 0。"""
        ...
