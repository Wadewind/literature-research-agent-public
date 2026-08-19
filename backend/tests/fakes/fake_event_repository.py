"""Event Repository 的内存假实现。"""

from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.domain.event import Event


class FakeEventRepository(EventRepository):
    """不依赖数据库的 Event Repository 假实现。"""

    def __init__(self) -> None:
        self._events: list[Event] = []

    async def add(self, event: Event) -> Event:
        """将 Event 存入内存。"""
        self._events.append(event)
        return event

    async def list_by_run(self, run_id: str) -> list[Event]:
        """返回指定 Run 的所有 Event。"""
        return sorted(
            [e for e in self._events if e.run_id == run_id],
            key=lambda e: e.sequence,
        )

    async def list_after(
        self,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[Event]:
        """返回 sequence 大于指定值的 Event。"""
        events = [
            e
            for e in await self.list_by_run(run_id)
            if e.sequence > after_sequence
        ]
        return events[:limit]
