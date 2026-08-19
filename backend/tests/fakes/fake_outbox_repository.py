"""Outbox Repository 的内存假实现。"""

from datetime import datetime

from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.domain.queue_outbox import OutboxStatus, QueueOutbox


class FakeOutboxRepository(OutboxRepository):
    """不依赖数据库的 Outbox Repository 假实现。"""

    def __init__(self) -> None:
        self._entries: dict[str, QueueOutbox] = {}

    async def add(self, entry: QueueOutbox) -> QueueOutbox:
        """将 Outbox 记录存入内存。"""
        self._entries[entry.outbox_id] = entry
        return entry

    async def get_by_run_id(self, run_id: str) -> QueueOutbox | None:
        """按 Run ID 返回 Outbox 记录。"""
        for entry in self._entries.values():
            if entry.run_id == run_id:
                return entry
        return None

    async def list_due_pending(self, now: datetime, limit: int) -> list[QueueOutbox]:
        """返回到期的 PENDING 记录，按预定时间升序。"""
        due = [
            entry
            for entry in self._entries.values()
            if entry.status == OutboxStatus.PENDING and entry.scheduled_at <= now
        ]
        due.sort(key=lambda entry: entry.scheduled_at)
        return due[:limit]

    async def try_mark_dispatched(self, outbox_id: str, dispatched_at: datetime) -> bool:
        """仅当记录仍为 PENDING 时标记为已投递。"""
        entry = self._entries.get(outbox_id)
        if entry is None or entry.status != OutboxStatus.PENDING:
            return False
        self._entries[outbox_id] = entry.mark_dispatched(dispatched_at)
        return True

    async def save(self, entry: QueueOutbox) -> None:
        """保存 Outbox 记录的更新。"""
        self._entries[entry.outbox_id] = entry
