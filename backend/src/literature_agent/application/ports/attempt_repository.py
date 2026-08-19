"""Run Attempt Repository 端口。"""

from datetime import datetime
from typing import Protocol

from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt


class AttemptRepository(Protocol):
    """Run Attempt 持久化的抽象端口。"""

    async def add(self, attempt: RunAttempt) -> RunAttempt:
        """保存 Attempt。"""
        ...

    async def count_by_run(self, run_id: str) -> int:
        """统计一个 Run 的 Attempt 数量（重试预算判断用）。"""
        ...

    async def get_latest_by_run(self, run_id: str) -> RunAttempt | None:
        """查询一个 Run 最新（attempt_number 最大）的 Attempt。"""
        ...

    async def record_heartbeat(self, attempt_id: str, now: datetime) -> bool:
        """条件更新心跳时间；仅 RUNNING 状态的 Attempt 可更新。"""
        ...

    async def finish_if_running(
        self,
        attempt_id: str,
        status: AttemptStatus,
        now: datetime,
        error: dict | None = None,
    ) -> bool:
        """条件结束 Attempt；仅 RUNNING 状态可更新，保证唯一终态。"""
        ...

    async def list_expired_running(self, cutoff: datetime, limit: int) -> list[RunAttempt]:
        """查询 lease 过期的执行中 Attempt。

        返回关联 Run 仍处于 RUNNING、且 ``heartbeat_at < cutoff`` 的
        RUNNING Attempt，按心跳时间升序。
        """
        ...
