"""Run 执行应用服务（Worker 侧）。

Worker 从队列收到只携带 ``run_id`` 的 Job 后，由本服务从 PostgreSQL
读取事实并认领 Run。职责划分：

- 本服务负责原子认领（QUEUED → RUNNING + ``run_started`` Event）、
  创建 Attempt 与执行期间的心跳、执行器抛错时的失败策略兜底
  （分类后 FAILED 或 RETRY_WAIT）、以及执行器退出后按 Run 状态关闭 Attempt；
- 执行器（如 IngestionExecutor）负责业务流程、进度 Event 和
  终态的原子提交（结果、当前指针、Run 终态、``result_committed``
  Event 在同一事务），从而不暴露半成品。

Attempt 是运维记录（lease/对账依据），业务事实仍以 Run 和 Event 为准。
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.failure_policy import (
    RunFailureOutcome,
    apply_run_failure,
)
from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.event_notifier import (
    EventNotifier,
    NoopEventNotifier,
)
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
)
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.arxiv import ArxivError
from literature_agent.domain.event import create_event
from literature_agent.domain.run import Run, RunStatus
from literature_agent.domain.run_attempt import (
    AttemptStatus,
    RunAttempt,
    create_run_attempt,
)
from literature_agent.metrics import metrics
from literature_agent.observability import bind_log_context, log_event

TSession = TypeVar("TSession", bound=Session)

# 执行器签名：接收已认领的 RUNNING Run 与关联标识符，自行推进终态或等待状态
RunExecutor = Callable[[Run, str], Awaitable[None]]

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_MAX_LENGTH = 500


class ExecutionOutcome(StrEnum):
    """一次执行的结果类别。"""

    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    PAUSED = "paused"
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
        event_notifier: EventNotifier | None = None,
        terminal_callback: Callable[[str, RunStatus], Awaitable[None]] | None = None,
    ) -> None:
        """初始化 RunExecutionService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            attempt_repo_factory: 根据 session 创建 AttemptRepository 的工厂。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            executor: 业务执行器，事务外调用，负责推进终态或等待状态。
            worker_id: 当前 Worker 标识（写入 Attempt）。
            heartbeat_interval_seconds: 心跳间隔秒数。
            max_run_attempts: 最大执行尝试次数（含首次）。
            event_notifier: 事件通知器，默认 Noop（切片 9，SSE 降延迟用）。
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
        self._event_notifier = event_notifier or NoopEventNotifier()
        self._terminal_callback = terminal_callback

    async def execute(self, run_id: str, correlation_id: str) -> ExecutionOutcome:
        """执行一个 Run：认领 QUEUED → RUNNING，然后交给执行器。

        参数:
            run_id: 目标 Run 标识符。
            correlation_id: 关联标识符，通常来自队列 Job。

        返回:
            执行结果类别；重复 Job、已终态或并发冲突返回 ``SKIPPED``。
        """
        clock_started = time.monotonic()
        with bind_log_context(correlation_id=correlation_id, run_id=run_id):
            started = await self._start(run_id, correlation_id)
            if started is None:
                return self._log_outcome(
                    ExecutionOutcome.MISSING,
                    clock_started,
                    status="missing",
                )
            run, attempt = started
            if run.status != RunStatus.RUNNING or attempt is None:
                return self._log_outcome(
                    ExecutionOutcome.SKIPPED, clock_started, status=run.status.value
                )

            with bind_log_context(
                project_id=run.project_id,
                attempt_id=attempt.attempt_id,
                run_type=getattr(run.run_type, "value", run.run_type),
            ):
                metrics.record_run_started(run.run_type)
                log_event(
                    logger,
                    logging.INFO,
                    "run_execution_started",
                    status=run.status.value,
                )
                heartbeat = asyncio.create_task(
                    self._heartbeat_loop(attempt.attempt_id)
                )
                try:
                    await self._executor(run, correlation_id)
                except Exception as exc:
                    outcome = await self._fail(run_id, attempt, exc, correlation_id)
                    await self._notify_terminal(run_id, await self._get_status(run_id))
                    metrics.record_run_completed(
                        run.run_type,
                        self._metrics_status(outcome),
                        time.monotonic() - clock_started,
                        attempt_status=(
                            "failed" if outcome is not ExecutionOutcome.SKIPPED else "cancelled"
                        ),
                    )
                    return self._log_outcome(
                        outcome,
                        clock_started,
                        error_code=type(exc).__name__,
                    )
                finally:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat

                final_status = await self._get_status(run_id)
                await self._close_attempt(attempt.attempt_id, final_status)
                await self._notify_terminal(run_id, final_status)
                if final_status == RunStatus.SUCCEEDED:
                    outcome = ExecutionOutcome.COMPLETED
                elif final_status == RunStatus.FAILED:
                    outcome = ExecutionOutcome.FAILED
                elif final_status == RunStatus.RETRY_WAIT:
                    outcome = ExecutionOutcome.RETRY_SCHEDULED
                elif final_status in {
                    RunStatus.WAITING_INPUT,
                    RunStatus.WAITING_DEPENDENCY,
                }:
                    outcome = ExecutionOutcome.PAUSED
                else:
                    # 执行期间被并发取消，或执行器没有推进到已知结束状态
                    outcome = ExecutionOutcome.SKIPPED
                metrics.record_run_completed(
                    run.run_type,
                    self._metrics_status(outcome, final_status),
                    time.monotonic() - clock_started,
                    attempt_status=self._metrics_attempt_status(final_status),
                )
                return self._log_outcome(
                    outcome,
                    clock_started,
                    status=final_status.value if final_status else "missing",
                )

    @staticmethod
    def _metrics_status(outcome: ExecutionOutcome, final_status: RunStatus | None = None) -> str:
        """把一次已认领执行归一化为固定低基数结果。"""
        if final_status is RunStatus.CANCELLED:
            return "cancelled"
        return {
            ExecutionOutcome.COMPLETED: "succeeded",
            ExecutionOutcome.FAILED: "failed",
            ExecutionOutcome.RETRY_SCHEDULED: "retry_scheduled",
            ExecutionOutcome.PAUSED: "paused",
        }.get(outcome, "unknown")

    @staticmethod
    def _metrics_attempt_status(final_status: RunStatus | None) -> str:
        """按持久 Attempt 关闭映射生成低基数状态。"""
        if final_status is None:
            return "failed"
        return {
            RunStatus.SUCCEEDED: "succeeded",
            RunStatus.FAILED: "failed",
            RunStatus.RETRY_WAIT: "failed",
            RunStatus.WAITING_INPUT: "paused",
            RunStatus.WAITING_DEPENDENCY: "paused",
            RunStatus.CANCELLED: "cancelled",
        }.get(final_status, "failed")

    @staticmethod
    def _log_outcome(
        outcome: ExecutionOutcome,
        started: float,
        *,
        status: str | None = None,
        error_code: str | None = None,
    ) -> ExecutionOutcome:
        event = {
            ExecutionOutcome.COMPLETED: "run_execution_completed",
            ExecutionOutcome.FAILED: "run_execution_failed",
            ExecutionOutcome.RETRY_SCHEDULED: "run_execution_retry",
            ExecutionOutcome.PAUSED: "run_execution_paused",
        }.get(outcome, "run_execution_skipped")
        log_event(
            logger,
            logging.INFO if outcome not in {ExecutionOutcome.FAILED} else logging.WARNING,
            event,
            status=status or outcome.value,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=error_code,
            exception_type=error_code,
        )
        return outcome

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
        await notify_run_event(self._event_notifier, run_id)
        claimed_run = Run(
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
        )
        return claimed_run, attempt

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
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "attempt_heartbeat_failed",
                    exc=exc,
                    attempt_id=attempt_id,
                    error_code=type(exc).__name__,
                )

    async def _close_attempt(
        self,
        attempt_id: str,
        final_status: RunStatus | None,
    ) -> None:
        """按 Run 结束状态 best-effort 关闭 Attempt。

        Run 已先提交等待或终态、但进程在关闭 Attempt 前崩溃时，周期 Reconciler
        会按业务 Run 当前事实幂等收敛这条残留 RUNNING Attempt。
        """
        mapping = {
            RunStatus.SUCCEEDED: AttemptStatus.SUCCEEDED,
            RunStatus.FAILED: AttemptStatus.FAILED,
            RunStatus.RETRY_WAIT: AttemptStatus.FAILED,
            RunStatus.WAITING_INPUT: AttemptStatus.PAUSED,
            RunStatus.WAITING_DEPENDENCY: AttemptStatus.PAUSED,
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
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "attempt_close_failed",
                exc=exc,
                attempt_id=attempt_id,
                error_code=type(exc).__name__,
            )

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
        error: dict[str, object]
        if isinstance(exc, ResearchAgentRuntimeError):
            error = {
                "type": exc.code,
                "message": exc.safe_message[:_ERROR_MESSAGE_MAX_LENGTH],
            }
        elif isinstance(exc, ArxivError):
            error = {"type": "ArxivError", "message": exc.code}
            if exc.http_status is not None:
                error["http_status"] = exc.http_status
            if exc.retry_after_seconds is not None:
                error["retry_after_seconds"] = exc.retry_after_seconds
        else:
            error = {
                "type": type(exc).__name__,
                "message": str(exc)[:_ERROR_MESSAGE_MAX_LENGTH],
            }
        outcome = RunFailureOutcome.SKIPPED
        cancellation_pending = False
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
            elif run is not None and run.status is RunStatus.CANCEL_REQUESTED:
                cancellation_pending = True
            await session.commit()
        if outcome != RunFailureOutcome.SKIPPED:
            await notify_run_event(self._event_notifier, run_id)
        if cancellation_pending:
            # Runtime 取消传播失败时保留 RUNNING Attempt；心跳已经停止，lease
            # Reconciler 会把 Run/Attempt 收敛为 CANCELLED，而不是错误重试。
            return ExecutionOutcome.SKIPPED
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
        except Exception as close_exc:
            log_event(
                logger,
                logging.WARNING,
                "attempt_close_failed",
                exc=close_exc,
                attempt_id=attempt.attempt_id,
                error_code=type(close_exc).__name__,
            )
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

    async def _notify_terminal(
        self, run_id: str, status: RunStatus | None
    ) -> None:
        """在业务事务结束后 best-effort 收敛扩展聚合的终态指针。"""
        if status is None or self._terminal_callback is None:
            return
        if status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return
        try:
            await self._terminal_callback(run_id, status)
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "run_terminal_callback_failed",
                exc=exc,
                error_code=type(exc).__name__,
            )
