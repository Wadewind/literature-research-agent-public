"""Run Attempt Repository 的内存假实现。"""

from dataclasses import replace
from datetime import datetime

from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt


class FakeAttemptRepository(AttemptRepository):
    """不依赖数据库的 Attempt Repository 假实现。"""

    def __init__(self) -> None:
        self._attempts: dict[str, RunAttempt] = {}
        self._orphaned_candidates: list[RunAttempt] = []

    async def add(self, attempt: RunAttempt) -> RunAttempt:
        """将 Attempt 存入内存。"""
        self._attempts[attempt.attempt_id] = attempt
        return attempt

    async def count_by_run(self, run_id: str) -> int:
        """统计一个 Run 的 Attempt 数量。"""
        return sum(1 for a in self._attempts.values() if a.run_id == run_id)

    async def get_latest_by_run(self, run_id: str) -> RunAttempt | None:
        """返回一个 Run 最新（attempt_number 最大）的 Attempt。"""
        candidates = [a for a in self._attempts.values() if a.run_id == run_id]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.attempt_number)

    async def list_by_run(self, run_id: str) -> list[RunAttempt]:
        """按序返回该 Run 的全部 Attempt。"""
        return sorted(
            (a for a in self._attempts.values() if a.run_id == run_id),
            key=lambda a: a.attempt_number,
        )

    async def record_heartbeat(self, attempt_id: str, now: datetime) -> bool:
        """仅 RUNNING 状态的 Attempt 可更新心跳。"""
        attempt = self._attempts.get(attempt_id)
        if attempt is None or attempt.status != AttemptStatus.RUNNING:
            return False
        self._attempts[attempt_id] = attempt.record_heartbeat(now)
        return True

    async def finish_if_running(
        self,
        attempt_id: str,
        status: AttemptStatus,
        now: datetime,
        error: dict | None = None,
    ) -> bool:
        """仅 RUNNING 状态的 Attempt 可结束。"""
        attempt = self._attempts.get(attempt_id)
        if attempt is None or attempt.status != AttemptStatus.RUNNING:
            return False
        self._attempts[attempt_id] = attempt.finish(status, now, error)
        return True

    async def list_expired_running(self, cutoff: datetime, limit: int) -> list[RunAttempt]:
        """返回心跳早于 cutoff 的 RUNNING Attempt（不 join Run）。"""
        expired = [
            a
            for a in self._attempts.values()
            if a.status == AttemptStatus.RUNNING and a.heartbeat_at < cutoff
        ]
        expired.sort(key=lambda a: a.heartbeat_at)
        return expired[:limit]

    async def list_orphaned_running(self, limit: int) -> list[RunAttempt]:
        """Fake 无 Run Repository 视图；测试可显式指定残留候选。"""
        return list(self._orphaned_candidates[:limit])

    # 测试辅助：直接操纵内部状态
    def seed(self, attempt: RunAttempt) -> None:
        """直接写入一条 Attempt（测试准备用）。"""
        self._attempts[attempt.attempt_id] = attempt

    def set_orphaned_candidates(self, attempts: list[RunAttempt]) -> None:
        """设置残留 Attempt 候选（测试辅助）。"""
        self._orphaned_candidates = list(attempts)

    def get(self, attempt_id: str) -> RunAttempt | None:
        """按 ID 返回 Attempt（测试断言用）。"""
        return self._attempts.get(attempt_id)

    def force_heartbeat(self, attempt_id: str, when: datetime) -> None:
        """强制设置心跳时间（模拟 lease 过期）。"""
        attempt = self._attempts[attempt_id]
        self._attempts[attempt_id] = replace(attempt, heartbeat_at=when)
