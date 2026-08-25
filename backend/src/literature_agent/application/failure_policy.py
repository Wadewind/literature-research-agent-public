"""Run 失败处理策略：错误分类驱动 FAILED 或 RETRY_WAIT 重试调度。

永久错误（输入类）直接 FAILED；临时错误在重试预算内推进
``RETRY_WAIT`` 并把 Outbox 记录重置为待投递（复用同一行，
退避沿用 Outbox 参数），由派发循环到点后重新投递。
Outbox 记录缺失或不可重置时降级为 FAILED，避免 Run 滞留
``RETRY_WAIT``。

调用方负责在同一事务中持有 Run 行锁并提交。
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
)
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.retry_policy import compute_retry_backoff, is_permanent_error
from literature_agent.domain.run import Run, RunStatus


class RunFailureOutcome(StrEnum):
    """失败处理的结果类别。"""

    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    SKIPPED = "skipped"


async def apply_run_failure[TSession: Session](
    session: TSession,
    *,
    run_repo_factory: Callable[[TSession], RunRepository],
    event_repo_factory: Callable[[TSession], EventRepository],
    attempt_repo_factory: Callable[[TSession], AttemptRepository],
    outbox_repo_factory: Callable[[TSession], OutboxRepository],
    run: Run,
    error: dict,
    exc: BaseException | None,
    correlation_id: str,
    max_run_attempts: int,
    now: datetime | None = None,
) -> RunFailureOutcome:
    """对一次执行失败应用分类与重试策略（须在持锁事务内调用）。

    参数:
        session: 当前事务会话。
        run_repo_factory: 根据 session 创建 RunRepository 的工厂。
        event_repo_factory: 根据 session 创建 EventRepository 的工厂。
        attempt_repo_factory: 根据 session 创建 AttemptRepository 的工厂。
        outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
        run: 已持锁的 Run 行；必须仍处于 RUNNING，否则返回 SKIPPED。
        error: 错误信息（类型与截断消息）。
        exc: 原始异常，用于永久/临时分类；None（如 Worker 崩溃对账）按临时处理。
        correlation_id: 关联标识符。
        max_run_attempts: 最大尝试次数（含首次执行）。
        now: 当前时间，默认取 UTC 现在。

    返回:
        处理结果类别。
    """
    if run.status != RunStatus.RUNNING:
        return RunFailureOutcome.SKIPPED

    run_repo = run_repo_factory(session)
    event_repo = event_repo_factory(session)
    attempt_repo = attempt_repo_factory(session)
    outbox_repo = outbox_repo_factory(session)

    now = now or datetime.now(UTC)
    attempts_used = await attempt_repo.count_by_run(run.run_id)
    permanent = exc is not None and (
        is_permanent_error(exc)
        or (
            isinstance(exc, ResearchAgentRuntimeError)
            and exc.kind in {RuntimeErrorKind.PERMANENT, RuntimeErrorKind.CANCELLED}
        )
    )

    retryable = not permanent and attempts_used < max_run_attempts
    next_at = now + timedelta(seconds=compute_retry_backoff(attempts_used))
    # 重试前提：Outbox 记录仍处于 DISPATCHED 可重置；缺失或状态异常时
    # 无法保证重新投递，降级为 FAILED，避免 Run 卡在 RETRY_WAIT
    if retryable and await outbox_repo.reset_for_retry(run.run_id, next_at):
        target = RunStatus.RETRY_WAIT
        event_type = "run_retry_scheduled"
        payload = {
            "error": error,
            "attempt": attempts_used,
            "next_retry_at": next_at.isoformat(),
        }
        outcome = RunFailureOutcome.RETRY_SCHEDULED
    else:
        target = RunStatus.FAILED
        event_type = "run_failed"
        payload = {"error": error}
        outcome = RunFailureOutcome.FAILED

    # 领域层校验转换合法性（RUNNING → FAILED/RETRY_WAIT）
    run.transition_to(target)
    updated = await run_repo.update_status(
        run_id=run.run_id,
        expected_status=RunStatus.RUNNING,
        new_status=target,
        new_event_sequence=run.event_sequence + 1,
    )
    if not updated:
        raise RunConcurrentModificationError(run.run_id)
    await event_repo.add(
        create_event(
            run_id=run.run_id,
            sequence=run.event_sequence,
            event_type=event_type,
            actor_type="system",
            correlation_id=correlation_id,
            payload=payload,
        )
    )
    return outcome
