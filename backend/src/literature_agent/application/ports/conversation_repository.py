"""Conversation Repository 端口。"""

from typing import Protocol

from literature_agent.domain.conversation import Conversation, ConversationScopePaper


class ConversationRepository(Protocol):
    """Conversation 与固化默认范围的持久化抽象。"""

    async def add(self, conversation: Conversation) -> Conversation:
        """保存 Conversation。"""
        ...

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        """按 ID 查询 Conversation；不存在返回 None。"""
        ...

    async def list_by_project(self, project_id: str) -> list[Conversation]:
        """列出 Project 的会话，按创建时间升序。"""
        ...

    async def add_scope_papers(
        self,
        scope_papers: list[ConversationScopePaper],
    ) -> None:
        """批量保存固化的默认范围条目（创建时一次性写入）。"""
        ...

    async def list_scope_papers(
        self,
        conversation_id: str,
    ) -> list[ConversationScopePaper]:
        """列出会话固化的默认范围条目。"""
        ...

    async def try_claim_active_run(self, conversation_id: str, run_id: str) -> bool:
        """条件更新认领活跃 Run：仅当 ``active_run_id IS NULL`` 时生效。

        并发双发提问只有一个认领成功（数据库条件更新兜底）。
        """
        ...

    async def release_active_run(
        self,
        conversation_id: str,
        *,
        expected_run_id: str | None = None,
    ) -> bool:
        """清理活跃 Run 认领；``expected_run_id`` 非空时仅当当前认领
        等于该值才清理（避免误清新一轮提问的认领）。返回是否生效。"""
        ...

    async def set_title_if_null(self, conversation_id: str, title: str) -> None:
        """仅当标题为 null 时回填（首条问题派生标题，不覆盖显式命名）。"""
        ...
