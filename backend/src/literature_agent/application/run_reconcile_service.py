"""Run 对账恢复应用服务。

Worker 崩溃会让 Run 停留在 RUNNING、Attempt 停止心跳。对账循环
（Worker 进程内）周期性地把 lease 过期的执行中 Run 收回：关闭旧
Attempt（worker_crashed），再按失败策略重新调度或按预算失败。

并发安全：候选查询之后逐个在持锁事务内二次校验（Run 仍 RUNNING、
Attempt 仍是最新且 lease 过期），条件更新保证多实例对账不产生
重复终态。
"""

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

from literature_agent.application.failure_policy import apply_run_failure
from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.run import RunStatus
from literature_agent.domain.run_attempt import AttemptStatus

logger = logging.getLogger(__name__)

_WORKER_CRASHED_ERROR = {"type": "worker_crashed", "message": "Worker lease 过期，执行被收回"}


class RunReconcileService[TSession: Session]:
    """收回过期 Run，并收敛业务状态提交后的残留 Attempt。"""

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
        terminal_callback: Callable[[str, RunStatus], Awaitable[None]] | None = None,
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
        self._terminal_callback = terminal_callback

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
                await self._notify_terminal(candidate.run_id)
        return recovered

    async def reconcile_orphaned_attempts(self, now: datetime | None = None) -> int:
        """有界、幂等关闭 Run 已离开执行边界的残留 RUNNING Attempt。"""
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            candidates = await self._attempt_repo_factory(session).list_orphaned_running(
                self._batch_size
            )

        closed = 0
        for candidate in candidates:
            if await self._close_orphan(candidate.run_id, candidate.attempt_id, now):
                closed += 1
        return closed

    async def _close_orphan(self, run_id: str, attempt_id: str, now: datetime) -> bool:
        """锁定 Run 后二次校验，不能关闭当前最新合法 Attempt。"""
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            attempt_repo = self._attempt_repo_factory(session)
            owner = await run_repo.get_by_id(run_id)
            if owner is None:
                return False
            run = await run_repo.get_by_id_for_update(run_id, owner.owner_id)
            if run is None:
                await session.rollback()
                return False
            latest = await attempt_repo.get_latest_by_run(run_id)
            if (
                latest is None
                or (
                    latest.attempt_id == attempt_id
                    and run.status in {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}
                )
            ):
                await session.rollback()
                return False

            status = await self._orphan_terminal_status(
                session, run_id, attempt_id, run.status
            )
            if status is None:
                await session.rollback()
                return False
            changed = await attempt_repo.finish_if_running(attempt_id, status, now)
            if not changed:
                await session.rollback()
                return False
            await session.commit()
            return True

    async def _orphan_terminal_status(
        self,
        session: TSession,
        run_id: str,
        attempt_id: str,
        run_status: RunStatus,
    ) -> AttemptStatus | None:
        """用 Attempt 时间区间内的持久 Event 区分正常暂停与失败重试。"""
        attempt_repo = self._attempt_repo_factory(session)
        attempts = await attempt_repo.list_by_run(run_id)
        candidate = next((item for item in attempts if item.attempt_id == attempt_id), None)
        if candidate is None:
            return None
        next_started_at = min(
            (
                item.started_at
                for item in attempts
                if item.attempt_number > candidate.attempt_number
            ),
            default=None,
        )
        events = await self._event_repo_factory(session).list_by_run(run_id)
        event_types = {
            event.event_type
            for event in events
            if event.occurred_at >= candidate.started_at
            and (next_started_at is None or event.occurred_at < next_started_at)
        }
        if event_types & {"dependency_wait_started", "human_input_requested"}:
            return AttemptStatus.PAUSED
        if event_types & {"run_retry_scheduled", "run_failed"}:
            return AttemptStatus.FAILED
        if "run_cancelled" in event_types:
            return AttemptStatus.CANCELLED
        if run_status == RunStatus.SUCCEEDED:
            return AttemptStatus.SUCCEEDED
        if run_status == RunStatus.FAILED:
            return AttemptStatus.FAILED
        if run_status == RunStatus.CANCELLED:
            return AttemptStatus.CANCELLED
        if run_status in {RunStatus.WAITING_INPUT, RunStatus.WAITING_DEPENDENCY}:
            return AttemptStatus.PAUSED
        return None

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

            recoverable_statuses = {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}
            run = await run_repo.get_by_id(run_id)
            if run is None or run.status not in recoverable_statuses:
                return False
            run_row = await run_repo.get_by_id_for_update(run_id, run.owner_id)
            if run_row is None or run_row.status not in recoverable_statuses:
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

            if run_row.status == RunStatus.CANCEL_REQUESTED:
                changed = await attempt_repo.finish_if_running(
                    attempt_id, AttemptStatus.CANCELLED, now
                )
                if not changed:
                    await session.rollback()
                    return False
                run_row.transition_to(RunStatus.CANCELLED)
                updated = await run_repo.update_status(
                    run_id=run_id,
                    expected_status=RunStatus.CANCEL_REQUESTED,
                    new_status=RunStatus.CANCELLED,
                    new_event_sequence=run_row.event_sequence + 1,
                )
                if not updated:
                    raise RunConcurrentModificationError(run_id)
                await self._event_repo_factory(session).add(
                    create_event(
                        run_id=run_id,
                        sequence=run_row.event_sequence,
                        event_type="run_cancelled",
                        actor_type="system",
                        correlation_id=f"reconcile:{attempt_id}",
                        payload={},
                    )
                )
                await session.commit()
                logger.info(
                    "收敛取消后崩溃的 Run: run_id=%s attempt_id=%s",
                    run_id,
                    attempt_id,
                )
                return True

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

    async def _notify_terminal(self, run_id: str) -> None:
        """业务收敛提交后 best-effort 释放扩展聚合的终态引用。"""
        if self._terminal_callback is None:
            return
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
        if run is None or run.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return
        try:
            await self._terminal_callback(run_id, run.status)
        except Exception as exc:
            logger.warning(
                "终态回调失败: run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )
