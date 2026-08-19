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
