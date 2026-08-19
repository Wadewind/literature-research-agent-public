"""Run 执行应用服务（Worker 侧）。

Worker 从队列收到只携带 ``run_id`` 的 Job 后，由本服务从 PostgreSQL
读取事实并认领 Run。职责划分：

- 本服务负责原子认领（QUEUED → RUNNING + ``run_started`` Event）、
  创建 Attempt 与执行期间的心跳、执行器抛错时的失败策略兜底
  （分类后 FAILED 或 RETRY_WAIT）、以及终态后关闭 Attempt；
- 执行器（如 IngestionExecutor）负责业务流程、进度 Event 和
  终态的原子提交（结果、当前指针、Run 终态、``result_committed``
  Event 在同一事务），从而不暴露半成品。

Attempt 是运维记录（lease/对账依据），业务事实仍以 Run 和 Event 为准。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from literature_agent.application.failure_policy import (
    RunFailureOutcome,
    apply_run_failure,
)
from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.run import Run, RunStatus
from literature_agent.domain.run_attempt import (
    AttemptStatus,
    RunAttempt,
    create_run_attempt,
)

TSession = TypeVar("TSession", bound=Session)

# 执行器签名：接收已认领的 RUNNING Run 与关联标识符，自行推进终态
RunExecutor = Callable[[Run, str], Awaitable[None]]

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_MAX_LENGTH = 500


class ExecutionOutcome(StrEnum):
    """一次执行的结果类别。"""

    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    MISSING = "missing"
    SKIPPED = "skipped"


class RunExecutionService:
    """认领单个 Run 并调用执行器，由 Worker Job 触发。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        attempt_repo_factory: Callable[[TSession], AttemptRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        executor: RunExecutor,
        worker_id: str,
        heartbeat_interval_seconds: float = 30.0,
        max_run_attempts: int = 3,
    ) -> None:
        """初始化 RunExecutionService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            attempt_repo_factory: 根据 session 创建 AttemptRepository 的工厂。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            executor: 业务执行器，事务外调用，负责推进终态。
            worker_id: 当前 Worker 标识（写入 Attempt）。
            heartbeat_interval_seconds: 心跳间隔秒数。
            max_run_attempts: 最大执行尝试次数（含首次）。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._attempt_repo_factory = attempt_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._executor = executor
        self._worker_id = worker_id
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._max_run_attempts = max_run_attempts

    async def execute(self, run_id: str, correlation_id: str) -> ExecutionOutcome:
        """执行一个 Run：认领 QUEUED → RUNNING，然后交给执行器。

        参数:
            run_id: 目标 Run 标识符。
            correlation_id: 关联标识符，通常来自队列 Job。

        返回:
            执行结果类别；重复 Job、已终态或并发冲突返回 ``SKIPPED``。
        """
        started = await self._start(run_id, correlation_id)
        if started is None:
            return ExecutionOutcome.MISSING
        run, attempt = started
        if run.status != RunStatus.RUNNING or attempt is None:
            return ExecutionOutcome.SKIPPED

        heartbeat = asyncio.create_task(self._heartbeat_loop(attempt.attempt_id))
        try:
            await self._executor(run, correlation_id)
        except Exception as exc:
            logger.warning("Run 执行失败: run_id=%s", run_id, exc_info=True)
            outcome = await self._fail(run_id, attempt, exc, correlation_id)
            return outcome
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

        final_status = await self._get_status(run_id)
        await self._close_attempt(attempt.attempt_id, final_status)
        if final_status == RunStatus.SUCCEEDED:
            return ExecutionOutcome.COMPLETED
        if final_status == RunStatus.FAILED:
            return ExecutionOutcome.FAILED
        if final_status == RunStatus.RETRY_WAIT:
            return ExecutionOutcome.RETRY_SCHEDULED
        # 执行期间被并发取消，或执行器未推进终态
        return ExecutionOutcome.SKIPPED

    async def _start(
        self,
        run_id: str,
        correlation_id: str,
    ) -> tuple[Run, RunAttempt | None] | None:
        """尝试把 Run 从 QUEUED 原子推进到 RUNNING，同事务创建 Attempt。

        返回 None 表示 Run 不存在；Attempt 为 None 表示本次调用应跳过
        （重复 Job 或并发冲突）。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(run_id)
            if run is None:
                return None
            if run.status != RunStatus.QUEUED:
                return run, None
            new_run = run.transition_to(RunStatus.RUNNING)
            claimed = await run_repo.update_status(
                run_id=run.run_id,
                expected_status=RunStatus.QUEUED,
                new_status=RunStatus.RUNNING,
                new_event_sequence=run.event_sequence + 1,
            )
            if not claimed:
                # 并发下另一个执行体已认领
                return run, None
            attempt_repo = self._attempt_repo_factory(session)
            attempt_number = await attempt_repo.count_by_run(run.run_id) + 1
            attempt = create_run_attempt(run.run_id, attempt_number, self._worker_id)
            await attempt_repo.add(attempt)
            event = create_event(
                run_id=run.run_id,
                sequence=run.event_sequence,
                event_type="run_started",
                actor_type="system",
                correlation_id=correlation_id,
                payload={"attempt": attempt_number},
            )
            await self._event_repo_factory(session).add(event)
            await session.commit()
            return Run(
                run_id=new_run.run_id,
                project_id=new_run.project_id,
                owner_id=new_run.owner_id,
                run_type=new_run.run_type,
                status=new_run.status,
                input_payload=new_run.input_payload,
                result_payload=new_run.result_payload,
                event_sequence=new_run.event_sequence + 1,
                created_at=new_run.created_at,
                updated_at=new_run.updated_at,
            ), attempt

    async def _heartbeat_loop(self, attempt_id: str) -> None:
        """周期性更新 Attempt 心跳；失败只记日志，不影响执行主路径。"""
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            try:
                async with self._session_factory() as session:
                    attempt_repo = self._attempt_repo_factory(session)
                    await attempt_repo.record_heartbeat(attempt_id, datetime.now(UTC))
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Attempt 心跳失败: attempt_id=%s", attempt_id, exc_info=True)

    async def _close_attempt(
        self,
        attempt_id: str,
        final_status: RunStatus | None,
    ) -> None:
        """按 Run 终态关闭 Attempt（best effort，崩溃场景由对账循环兜底）。"""
        mapping = {
            RunStatus.SUCCEEDED: AttemptStatus.SUCCEEDED,
            RunStatus.FAILED: AttemptStatus.FAILED,
            RunStatus.RETRY_WAIT: AttemptStatus.FAILED,
            RunStatus.CANCELLED: AttemptStatus.CANCELLED,
        }
        attempt_status = AttemptStatus.FAILED
        if final_status is not None:
            attempt_status = mapping.get(final_status, AttemptStatus.FAILED)
        try:
            async with self._session_factory() as session:
                attempt_repo = self._attempt_repo_factory(session)
                await attempt_repo.finish_if_running(
                    attempt_id, attempt_status, datetime.now(UTC)
                )
                await session.commit()
        except Exception:
            logger.warning("关闭 Attempt 失败: attempt_id=%s", attempt_id, exc_info=True)

    async def _fail(
        self,
        run_id: str,
        attempt: RunAttempt,
        exc: Exception,
        correlation_id: str,
    ) -> ExecutionOutcome:
        """兜底：对仍处于 RUNNING 的 Run 应用失败策略并关闭 Attempt。

        条件更新失败（例如执行器已自行收尾或被并发取消）时不产生第二个终态。
        """
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:_ERROR_MESSAGE_MAX_LENGTH],
        }
        outcome = RunFailureOutcome.SKIPPED
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            owner = await run_repo.get_by_id(run_id)
            run = (
                await run_repo.get_by_id_for_update(run_id, owner.owner_id)
                if owner is not None
                else None
            )
            if run is not None and run.status == RunStatus.RUNNING:
                outcome = await apply_run_failure(
                    session,
                    run_repo_factory=self._run_repo_factory,
                    event_repo_factory=self._event_repo_factory,
                    attempt_repo_factory=self._attempt_repo_factory,
                    outbox_repo_factory=self._outbox_repo_factory,
                    run=run,
                    error=error,
                    exc=exc,
                    correlation_id=correlation_id,
                    max_run_attempts=self._max_run_attempts,
                )
            await session.commit()
        attempt_status = (
            AttemptStatus.FAILED
            if outcome != RunFailureOutcome.SKIPPED
            else AttemptStatus.CANCELLED
        )
        try:
            async with self._session_factory() as session:
                attempt_repo = self._attempt_repo_factory(session)
                await attempt_repo.finish_if_running(
                    attempt.attempt_id, attempt_status, datetime.now(UTC), error
                )
                await session.commit()
        except Exception:
            logger.warning("关闭 Attempt 失败: attempt_id=%s", attempt.attempt_id, exc_info=True)
        if outcome == RunFailureOutcome.RETRY_SCHEDULED:
            return ExecutionOutcome.RETRY_SCHEDULED
        if outcome == RunFailureOutcome.FAILED:
            return ExecutionOutcome.FAILED
        return ExecutionOutcome.SKIPPED

    async def _get_status(self, run_id: str) -> RunStatus | None:
        """读取 Run 当前状态；不存在返回 None。"""
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
            return run.status if run else None
