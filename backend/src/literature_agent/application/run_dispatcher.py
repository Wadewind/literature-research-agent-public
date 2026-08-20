"""Worker 侧 run_type 分发器：组合执行器，按 Run 类型显式分发。

``RunExecutionService`` 的单执行器签名不变，本分发器作为组合执行器
注入：``ingestion`` 分发给 IngestionExecutor，``indexing`` 分发给
IndexingExecutor；未知类型显式把 Run 推进 FAILED（错误类型
``unknown_run_type``），不静默执行。``rag_answer`` 在切片 8 接线，
本阶段未注册时同样按未知类型失败。
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.event_notifier import (
    EventNotifier,
    NoopEventNotifier,
)
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.run_execution_service import RunExecutor
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.run import Run, RunStatus, RunType

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)


class RunDispatcher[TSession: Session]:
    """按 ``run_type`` 把 Run 分发给已注册执行器的组合执行器。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        executors: dict[RunType, RunExecutor],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        """初始化 RunDispatcher。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            executors: RunType 到执行器的映射；未注册的类型按未知类型失败。
            event_notifier: 事件通知器，默认 Noop。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._executors = dict(executors)
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def execute(self, run: Run, correlation_id: str) -> None:
        """按 Run 类型分发执行；未知类型推进 FAILED。

        参数:
            run: 已认领的 RUNNING 状态 Run。
            correlation_id: 关联标识符。
        """
        try:
            run_type = RunType(run.run_type)
        except ValueError:
            run_type = None  # 未登记的非法取值
        executor = self._executors.get(run_type) if run_type is not None else None
        if executor is None:
            logger.warning("未知 run_type，Run 推进 FAILED: run_id=%s", run.run_id)
            await self._fail_unknown(run, correlation_id)
            return
        await executor(run, correlation_id)

    async def _fail_unknown(self, run: Run, correlation_id: str) -> None:
        """把未知类型的 Run 推进 FAILED 并写 run_failed 事件。"""
        error = {
            "type": "unknown_run_type",
            "message": f"未知 run_type: {run.run_type}",
        }
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None:
                raise RunConcurrentModificationError(run.run_id)
            if run_row.status != RunStatus.RUNNING:
                # 并发下已被推进（例如取消），不产生第二个终态
                await session.commit()
                return
            # 领域层校验转换合法性（RUNNING → FAILED）
            run_row.transition_to(RunStatus.FAILED)
            updated = await run_repo.update_status(
                run_id=run_row.run_id,
                expected_status=RunStatus.RUNNING,
                new_status=RunStatus.FAILED,
                new_event_sequence=run_row.event_sequence + 1,
            )
            if not updated:
                raise RunConcurrentModificationError(run_row.run_id)
            await self._event_repo_factory(session).add(
                create_event(
                    run_id=run_row.run_id,
                    sequence=run_row.event_sequence,
                    event_type="run_failed",
                    actor_type="system",
                    correlation_id=correlation_id,
                    payload={"error": error},
                )
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
