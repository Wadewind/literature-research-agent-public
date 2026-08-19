"""Run 执行应用服务（Worker 侧）。

Worker 从队列收到只携带 ``run_id`` 的 Job 后，由本服务从 PostgreSQL
读取事实并认领 Run。职责划分：

- 本服务负责原子认领（QUEUED → RUNNING + ``run_started`` Event）、
  重复 Job 跳过、以及执行器抛错时的 FAILED 兜底；
- 执行器（如 IngestionExecutor）负责业务流程、进度 Event 和
  终态的原子提交（结果、当前指针、Run 终态、``result_committed``
  Event 在同一事务），从而不暴露半成品。
"""

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from typing import TypeVar

from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.run import Run, RunStatus

TSession = TypeVar("TSession", bound=Session)

# 执行器签名：接收已认领的 RUNNING Run 与关联标识符，自行推进终态
RunExecutor = Callable[[Run, str], Awaitable[None]]

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_MAX_LENGTH = 500


class ExecutionOutcome(StrEnum):
    """一次执行的结果类别。"""

    COMPLETED = "completed"
    FAILED = "failed"
    MISSING = "missing"
    SKIPPED = "skipped"


class RunExecutionService:
    """认领单个 Run 并调用执行器，由 Worker Job 触发。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        executor: RunExecutor,
    ) -> None:
        """初始化 RunExecutionService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            executor: 业务执行器，事务外调用，负责推进终态。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._executor = executor

    async def execute(self, run_id: str, correlation_id: str) -> ExecutionOutcome:
        """执行一个 Run：认领 QUEUED → RUNNING，然后交给执行器。

        参数:
            run_id: 目标 Run 标识符。
            correlation_id: 关联标识符，通常来自队列 Job。

        返回:
            执行结果类别；重复 Job、已终态或并发冲突返回 ``SKIPPED``。
        """
        run = await self._start(run_id, correlation_id)
        if run is None:
            return ExecutionOutcome.MISSING
        if run.status != RunStatus.RUNNING:
            return ExecutionOutcome.SKIPPED

        try:
            await self._executor(run, correlation_id)
        except Exception as exc:
            logger.warning("Run 执行失败: run_id=%s", run_id, exc_info=True)
            error_payload = {
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:_ERROR_MESSAGE_MAX_LENGTH],
                }
            }
            await self._fail(run_id, error_payload, correlation_id)
            return ExecutionOutcome.FAILED

        final_status = await self._get_status(run_id)
        if final_status == RunStatus.SUCCEEDED:
            return ExecutionOutcome.COMPLETED
        if final_status == RunStatus.FAILED:
            return ExecutionOutcome.FAILED
        # 执行期间被并发取消，或执行器未推进终态
        return ExecutionOutcome.SKIPPED

    async def _start(self, run_id: str, correlation_id: str) -> Run | None:
        """尝试把 Run 从 QUEUED 原子推进到 RUNNING。

        返回 None 表示 Run 不存在；返回非 RUNNING 状态的 Run 表示
        本次调用应跳过（重复 Job 或并发冲突）。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(run_id)
            if run is None:
                return None
            if run.status != RunStatus.QUEUED:
                return run
            new_run = run.transition_to(RunStatus.RUNNING)
            claimed = await run_repo.update_status(
                run_id=run.run_id,
                expected_status=RunStatus.QUEUED,
                new_status=RunStatus.RUNNING,
                new_event_sequence=run.event_sequence + 1,
            )
            if not claimed:
                # 并发下另一个执行体已认领
                return run
            event = create_event(
                run_id=run.run_id,
                sequence=run.event_sequence,
                event_type="run_started",
                actor_type="system",
                correlation_id=correlation_id,
                payload={},
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
            )

    async def _fail(self, run_id: str, payload: dict, correlation_id: str) -> bool:
        """兜底：把仍处于 RUNNING 的 Run 推进到 FAILED。

        条件更新失败（例如执行器已自行收尾或被并发取消）时返回 False，
        不产生第二个终态。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(run_id)
            if run is None or run.status != RunStatus.RUNNING:
                return False
            updated = await run_repo.update_status(
                run_id=run.run_id,
                expected_status=RunStatus.RUNNING,
                new_status=RunStatus.FAILED,
                new_event_sequence=run.event_sequence + 1,
            )
            if not updated:
                return False
            event = create_event(
                run_id=run.run_id,
                sequence=run.event_sequence,
                event_type="run_failed",
                actor_type="system",
                correlation_id=correlation_id,
                payload=payload,
            )
            await self._event_repo_factory(session).add(event)
            await session.commit()
            return True

    async def _get_status(self, run_id: str) -> RunStatus | None:
        """读取 Run 当前状态；不存在返回 None。"""
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
            return run.status if run else None
