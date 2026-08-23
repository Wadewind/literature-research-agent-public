"""Queue Outbox 派发应用服务。

把数据库中待投递的 Outbox 记录派发到执行队列，是数据库提交与
队列投递之间的桥梁。派发按至少一次投递设计：

- 投递成功但标记失败（崩溃、断网）时，Outbox 记录保持 PENDING，
  下一轮重新投递；重复 Job 由队列 Job ID 去重和 Worker 幂等执行兜底；
- 投递失败时记录尝试次数并按指数退避推迟下一次尝试；
- 达到最大尝试次数后进入 FAILED 终态，等待人工介入。
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TypeVar

from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.run_queue import RunQueue
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.queue_outbox import QueueOutbox
from literature_agent.domain.run import RunStatus
from literature_agent.metrics import metrics
from literature_agent.observability import log_event

TSession = TypeVar("TSession", bound=Session)

# 无执行意义、直接丢弃投递的 Run 状态
_DROPPABLE_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.CANCEL_REQUESTED,
}

logger = logging.getLogger(__name__)


class OutboxDispatchService:
    """读取到期 Outbox 记录并向队列投递的用例层服务。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        queue: RunQueue,
        max_attempts: int,
        batch_size: int,
        run_repo_factory: Callable[[TSession], RunRepository] | None = None,
        event_repo_factory: Callable[[TSession], EventRepository] | None = None,
    ) -> None:
        """初始化 OutboxDispatchService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            queue: 执行队列适配器。
            max_attempts: 单条记录允许的最大投递尝试次数。
            batch_size: 单次派发的最大记录数。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂，
                用于重投时把 RETRY_WAIT Run 转回 QUEUED（切片 8 起需要）。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
        """
        self._session_factory = session_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._queue = queue
        self._max_attempts = max_attempts
        self._batch_size = batch_size
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory

    async def dispatch_pending(self, now: datetime | None = None) -> int:
        """派发一批到期的 Outbox 记录，返回成功投递的数量。

        外部队列调用发生在数据库事务外；每条记录的标记各自独立提交，
        避免单条失败影响整批。Run 处于 RETRY_WAIT 时先条件转回 QUEUED
        再投递；已终态或已取消的 Run 直接标记为已投递（丢弃）。

        参数:
            now: 当前时间，默认取 UTC 现在；测试可注入固定时间。
        """
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            outbox_repo = self._outbox_repo_factory(session)
            entries = await outbox_repo.list_due_pending(now, self._batch_size)

        dispatched = 0
        for entry in entries:
            preparation = await self._prepare_run(entry, now)
            if preparation == "dropped":
                metrics.record_outbox("dropped")
                continue
            if preparation != "dispatch":
                continue
            try:
                await self._queue.enqueue_run(entry.run_id)
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "outbox_dispatch_failed",
                    exc=exc,
                    outbox_id=entry.outbox_id,
                    run_id=entry.run_id,
                    error_code=type(exc).__name__,
                )
                await self._record_failure(entry, now)
                metrics.record_outbox("failed")
                continue
            if await self._mark_dispatched(entry.outbox_id, now):
                dispatched += 1
                metrics.record_outbox("dispatched")
                log_event(
                    logger,
                    logging.INFO,
                    "outbox_dispatch_completed",
                    outbox_id=entry.outbox_id,
                    run_id=entry.run_id,
                    status="dispatched",
                )
        return dispatched

    async def _prepare_run(self, entry: QueueOutbox, now: datetime) -> str:
        """投递前检查/准备 Run 状态。

        返回 ``dispatch/dropped/skipped`` 表示应投递、已明确丢弃或并发跳过。
        未注入 run_repo_factory 时保持切片 5 行为：总是投递。
        """
        if self._run_repo_factory is None:
            return "dispatch"
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(entry.run_id)
            if run is None or run.status in _DROPPABLE_STATUSES:
                # 无执行意义：标记已投递，结束该记录
                marked = await self._outbox_repo_factory(session).try_mark_dispatched(
                    entry.outbox_id, now
                )
                await session.commit()
                return "dropped" if marked else "skipped"
            if run.status == RunStatus.RETRY_WAIT:
                # 重投：条件转回 QUEUED 并记录事件；并发取消时放弃本轮
                run.transition_to(RunStatus.QUEUED)
                updated = await run_repo.update_status(
                    run_id=run.run_id,
                    expected_status=RunStatus.RETRY_WAIT,
                    new_status=RunStatus.QUEUED,
                    new_event_sequence=run.event_sequence + 1,
                )
                if not updated:
                    await session.rollback()
                    return "skipped"
                if self._event_repo_factory is not None:
                    await self._event_repo_factory(session).add(
                        create_event(
                            run_id=run.run_id,
                            sequence=run.event_sequence,
                            event_type="run_requeued",
                            actor_type="system",
                            correlation_id=f"outbox:{entry.outbox_id}",
                            payload={},
                        )
                    )
                await session.commit()
            return "dispatch"

    async def _mark_dispatched(self, outbox_id: str, now: datetime) -> bool:
        """在独立事务中条件标记为已投递。"""
        async with self._session_factory() as session:
            outbox_repo = self._outbox_repo_factory(session)
            marked = await outbox_repo.try_mark_dispatched(outbox_id, now)
            await session.commit()
            return marked

    async def _record_failure(self, entry: QueueOutbox, now: datetime) -> None:
        """在独立事务中记录一次投递失败。"""
        async with self._session_factory() as session:
            outbox_repo = self._outbox_repo_factory(session)
            await outbox_repo.save(entry.record_dispatch_failure(now, self._max_attempts))
            await session.commit()
