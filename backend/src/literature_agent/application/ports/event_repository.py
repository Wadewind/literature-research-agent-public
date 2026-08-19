"""Event Repository 端口。"""

from typing import Protocol

from literature_agent.domain.event import Event


class EventRepository(Protocol):
    """Event 持久化的抽象端口。"""

    async def add(self, event: Event) -> Event:
        """保存 Event。"""
        ...

    async def list_by_run(self, run_id: str) -> list[Event]:
        """按 Run ID 列出所有 Event，按 sequence 升序。"""
        ...

    async def list_after(
        self,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[Event]:
        """列出 sequence 大于 ``after_sequence`` 的 Event，按 sequence 升序。

        用于分页游标与 SSE 断线重放；``limit`` 由调用方约束上限。
        """
        ...
