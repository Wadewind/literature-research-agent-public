"""Queue Outbox Repository 端口。"""

from datetime import datetime
from typing import Protocol

from literature_agent.domain.queue_outbox import QueueOutbox


class OutboxRepository(Protocol):
    """Queue Outbox 持久化的抽象端口。"""

    async def add(self, entry: QueueOutbox) -> QueueOutbox:
        """保存 Outbox 记录。"""
        ...

    async def get_by_run_id(self, run_id: str) -> QueueOutbox | None:
        """按 Run ID 查询 Outbox 记录；不存在时返回 None。"""
        ...

    async def list_due_pending(self, now: datetime, limit: int) -> list[QueueOutbox]:
        """查询已到期、等待派发的记录，按预定时间升序返回。

        只返回 ``status = PENDING`` 且 ``scheduled_at <= now`` 的记录。
        """
        ...

    async def try_mark_dispatched(self, outbox_id: str, dispatched_at: datetime) -> bool:
        """条件更新为已投递；仅当当前状态为 PENDING 时成功。

        返回是否更新成功。并发或重复派发时保证只有一个调用生效。
        """
        ...

    async def save(self, entry: QueueOutbox) -> None:
        """保存 Outbox 记录的更新（用于记录投递失败）。"""
        ...

    async def reset_for_retry(self, run_id: str, scheduled_at: datetime) -> bool:
        """把已投递的记录条件重置为待投递（Run 重试用）。

        仅当当前状态为 DISPATCHED 时成功；同时推迟 ``scheduled_at``、
        ``attempt_count`` 加一。返回是否更新成功。
        """
        ...
