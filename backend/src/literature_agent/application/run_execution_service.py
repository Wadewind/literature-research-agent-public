"""Run 执行应用服务（Worker 侧）。

Worker 从队列收到只携带 ``run_id`` 的 Job 后，由本服务从 PostgreSQL
读取事实并推进 Run 状态。执行按至少一次投递设计：

- 只有 ``QUEUED`` 状态的 Run 会被启动，重复 Job 直接跳过；
- 状态转换与对应 Event 在同一短事务提交；
- 业务执行（本切片为占位实现，切片 6 接入真实解析）发生在事务外；
- 执行期间 Run 被并发取消（条件更新失败）时不产生第二个终态。
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

# 业务执行体签名：接收 Run，返回小型结构化结果 payload
RunWork = Callable[[Run], Awaitable[dict]]

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_MAX_LENGTH = 500


class ExecutionOutcome(StrEnum):
    """一次执行的结果类别。"""

    COMPLETED = "completed"
    FAILED = "failed"
    MISSING = "missing"
    SKIPPED = "skipped"


class RunExecutionService:
    """推进单个 Run 生命周期的用例层服务，由 Worker Job 调用。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        work: RunWork,
    ) -> None:
        """初始化 RunExecutionService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            work: 业务执行体，事务外调用；本切片使用占位实现。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._work = work

    async def execute(self, run_id: str, correlation_id: str) -> ExecutionOutcome:
        """执行一个 Run：QUEUED → RUNNING → SUCCEEDED/FAILED。

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
            result_payload = await self._work(run)
        except Exception as exc:
            logger.warning("Run 执行失败: run_id=%s", run_id, exc_info=True)
            error_payload = {
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:_ERROR_MESSAGE_MAX_LENGTH],
                }
            }
            await self._finish(
                run_id, RunStatus.FAILED, "run_failed", error_payload, correlation_id
            )
            return ExecutionOutcome.FAILED

        finished = await self._finish(
            run_id,
            RunStatus.SUCCEEDED,
            "run_completed",
            {"result": result_payload},
            correlation_id,
        )
        return ExecutionOutcome.COMPLETED if finished else ExecutionOutcome.SKIPPED

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

    async def _finish(
        self,
        run_id: str,
        target_status: RunStatus,
        event_type: str,
        payload: dict,
        correlation_id: str,
    ) -> bool:
        """把 RUNNING 的 Run 原子推进到终态并写入事件。

        条件更新失败（例如执行期间被并发取消）时返回 False，
        不产生第二个终态。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(run_id)
            if run is None or run.status != RunStatus.RUNNING:
                return False
            # 领域层校验转换合法性，非法时抛出 InvalidRunTransitionError
            run.transition_to(target_status)
            updated = await run_repo.update_status(
                run_id=run.run_id,
                expected_status=RunStatus.RUNNING,
                new_status=target_status,
                new_event_sequence=run.event_sequence + 1,
            )
            if not updated:
                return False
            event = create_event(
                run_id=run.run_id,
                sequence=run.event_sequence,
                event_type=event_type,
                actor_type="system",
                correlation_id=correlation_id,
                payload=payload,
            )
            await self._event_repo_factory(session).add(event)
            await session.commit()
            return True
