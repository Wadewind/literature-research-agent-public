"""Conversation Repository 的内存假实现。"""

from dataclasses import replace

from literature_agent.application.ports.conversation_repository import (
    ConversationRepository,
)
from literature_agent.domain.conversation import Conversation, ConversationScopePaper


class FakeConversationRepository(ConversationRepository):
    """不依赖数据库的 Conversation Repository 假实现。

    ``try_claim_active_run`` 模拟数据库条件更新语义（仅在
    ``active_run_id IS NULL`` 时认领成功）；终态残留认领的自愈清理由
    应用层（ConversationService.post_message）负责。
    """

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._scope_papers: list[ConversationScopePaper] = []

    async def add(self, conversation: Conversation) -> Conversation:
        """将 Conversation 存入内存。"""
        self._conversations[conversation.conversation_id] = conversation
        return conversation

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        """按 ID 返回 Conversation。"""
        return self._conversations.get(conversation_id)

    async def list_by_project(self, project_id: str) -> list[Conversation]:
        """列出 Project 的会话，按创建时间升序。"""
        result = [
            c
            for c in self._conversations.values()
            if c.project_id == project_id
        ]
        result.sort(key=lambda c: c.created_at)
        return result

    async def add_scope_papers(
        self,
        scope_papers: list[ConversationScopePaper],
    ) -> None:
        """批量保存固化的默认范围条目。"""
        self._scope_papers.extend(scope_papers)

    async def list_scope_papers(
        self,
        conversation_id: str,
    ) -> list[ConversationScopePaper]:
        """列出会话固化的默认范围条目。"""
        return [
            entry
            for entry in self._scope_papers
            if entry.conversation_id == conversation_id
        ]

    async def try_claim_active_run(self, conversation_id: str, run_id: str) -> bool:
        """仅当当前无活跃 Run 时认领成功。"""
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.active_run_id is not None:
            return False
        self._conversations[conversation_id] = replace(
            conversation, active_run_id=run_id
        )
        return True

    async def release_active_run(
        self,
        conversation_id: str,
        *,
        expected_run_id: str | None = None,
    ) -> bool:
        """清理活跃 Run 认领；可按期望认领值条件清理。"""
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.active_run_id is None:
            return False
        if (
            expected_run_id is not None
            and conversation.active_run_id != expected_run_id
        ):
            return False
        self._conversations[conversation_id] = replace(
            conversation, active_run_id=None
        )
        return True

    async def set_title_if_null(self, conversation_id: str, title: str) -> None:
        """仅当标题为 None 时回填。"""
        conversation = self._conversations.get(conversation_id)
        if conversation is not None and conversation.title is None:
            self._conversations[conversation_id] = replace(conversation, title=title)
