"""Message Repository 的内存假实现。"""

from literature_agent.application.ports.message_repository import MessageRepository
from literature_agent.domain.conversation import Message, MessageRole


class FakeMessageRepository(MessageRepository):
    """不依赖数据库的 Message Repository 假实现。"""

    def __init__(self) -> None:
        self._messages: dict[str, Message] = {}

    async def add(self, message: Message) -> Message:
        """将 Message 存入内存。"""
        self._messages[message.message_id] = message
        return message

    async def get_by_id(self, message_id: str) -> Message | None:
        """按 ID 返回 Message。"""
        return self._messages.get(message_id)

    async def list_by_conversation(self, conversation_id: str) -> list[Message]:
        """按会话返回消息，按 sequence 升序。"""
        result = [
            m
            for m in self._messages.values()
            if m.conversation_id == conversation_id
        ]
        result.sort(key=lambda m: m.sequence)
        return result

    async def get_by_run_and_role(
        self,
        run_id: str,
        role: MessageRole,
    ) -> Message | None:
        """按关联 Run 与角色返回消息。"""
        return next(
            (
                m
                for m in self._messages.values()
                if m.run_id == run_id and m.role is role
            ),
            None,
        )

    async def max_sequence(self, conversation_id: str) -> int:
        """返回会话内当前最大 sequence；无消息返回 0。"""
        sequences = [
            m.sequence
            for m in self._messages.values()
            if m.conversation_id == conversation_id
        ]
        return max(sequences, default=0)
