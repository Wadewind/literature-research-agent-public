"""Run 对账恢复应用服务。

Worker 崩溃会让 Run 停留在 RUNNING、Attempt 停止心跳。对账循环
（Worker 进程内）周期性地把 lease 过期的执行中 Run 收回：关闭旧
Attempt（worker_crashed），再按失败策略重新调度或按预算失败。

并发安全：候选查询之后逐个在持锁事务内二次校验（Run 仍 RUNNING、
Attempt 仍是最新且 lease 过期），条件更新保证多实例对账不产生
重复终态。
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from literature_agent.application.failure_policy import apply_run_failure
from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.run import RunStatus
from literature_agent.domain.run_attempt import AttemptStatus

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)

_WORKER_CRASHED_ERROR = {"type": "worker_crashed", "message": "Worker lease 过期，执行被收回"}


class RunReconcileService:
    """收回 lease 过期的 RUNNING Run 并重新调度。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        attempt_repo_factory: Callable[[TSession], AttemptRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        lease_seconds: float,
        max_run_attempts: int,
        batch_size: int = 20,
    ) -> None:
        """初始化 RunReconcileService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            attempt_repo_factory: 根据 session 创建 AttemptRepository 的工厂。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            lease_seconds: Worker lease 秒数，超过无心跳视为崩溃。
            max_run_attempts: 最大执行尝试次数（含首次）。
            batch_size: 单轮对账的最大记录数。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._attempt_repo_factory = attempt_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._lease_seconds = lease_seconds
        self._max_run_attempts = max_run_attempts
        self._batch_size = batch_size

    async def reconcile_expired(self, now: datetime | None = None) -> int:
        """收回一批 lease 过期的 Run，返回实际收回数量。"""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._lease_seconds)
        async with self._session_factory() as session:
            attempt_repo = self._attempt_repo_factory(session)
            candidates = await attempt_repo.list_expired_running(cutoff, self._batch_size)

        recovered = 0
        for candidate in candidates:
            if await self._recover(candidate.run_id, candidate.attempt_id, cutoff, now):
                recovered += 1
        return recovered

    async def _recover(
        self,
        run_id: str,
        attempt_id: str,
        cutoff: datetime,
        now: datetime,
    ) -> bool:
        """在持锁事务内二次校验并收回单个 Run。"""
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            attempt_repo = self._attempt_repo_factory(session)

            run = await run_repo.get_by_id(run_id)
            if run is None or run.status != RunStatus.RUNNING:
                return False
            run_row = await run_repo.get_by_id_for_update(run_id, run.owner_id)
            if run_row is None or run_row.status != RunStatus.RUNNING:
                await session.rollback()
                return False
            latest = await attempt_repo.get_latest_by_run(run_id)
            if (
                latest is None
                or latest.attempt_id != attempt_id
                or latest.status != AttemptStatus.RUNNING
                or latest.heartbeat_at >= cutoff
            ):
                await session.rollback()
                return False

            await attempt_repo.finish_if_running(
                attempt_id, AttemptStatus.FAILED, now, _WORKER_CRASHED_ERROR
            )
            await apply_run_failure(
                session,
                run_repo_factory=self._run_repo_factory,
                event_repo_factory=self._event_repo_factory,
                attempt_repo_factory=self._attempt_repo_factory,
                outbox_repo_factory=self._outbox_repo_factory,
                run=run_row,
                error=dict(_WORKER_CRASHED_ERROR),
                exc=None,  # Worker 崩溃按临时错误处理
                correlation_id=f"reconcile:{attempt_id}",
                max_run_attempts=self._max_run_attempts,
                now=now,
            )
            await session.commit()
            logger.info("收回 lease 过期的 Run: run_id=%s attempt_id=%s", run_id, attempt_id)
            return True
