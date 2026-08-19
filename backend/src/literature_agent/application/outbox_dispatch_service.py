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

from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.run_queue import RunQueue
from literature_agent.application.ports.session import Session
from literature_agent.domain.queue_outbox import QueueOutbox

TSession = TypeVar("TSession", bound=Session)

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
    ) -> None:
        """初始化 OutboxDispatchService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            queue: 执行队列适配器。
            max_attempts: 单条记录允许的最大投递尝试次数。
            batch_size: 单次派发的最大记录数。
        """
        self._session_factory = session_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._queue = queue
        self._max_attempts = max_attempts
        self._batch_size = batch_size

    async def dispatch_pending(self, now: datetime | None = None) -> int:
        """派发一批到期的 Outbox 记录，返回成功投递的数量。

        外部队列调用发生在数据库事务外；每条记录的标记各自独立提交，
        避免单条失败影响整批。

        参数:
            now: 当前时间，默认取 UTC 现在；测试可注入固定时间。
        """
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            outbox_repo = self._outbox_repo_factory(session)
            entries = await outbox_repo.list_due_pending(now, self._batch_size)

        dispatched = 0
        for entry in entries:
            try:
                await self._queue.enqueue_run(entry.run_id)
            except Exception:
                logger.warning(
                    "Outbox 投递失败: outbox_id=%s run_id=%s",
                    entry.outbox_id,
                    entry.run_id,
                    exc_info=True,
                )
                await self._record_failure(entry, now)
                continue
            if await self._mark_dispatched(entry.outbox_id, now):
                dispatched += 1
        return dispatched

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
