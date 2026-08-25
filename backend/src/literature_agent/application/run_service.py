"""Run 应用服务。"""

import logging
from collections.abc import Awaitable, Callable
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
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.run import Run, RunStatus, create_run

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)


class RunService:
    """Run 用例层，负责状态转换、事件写入与事务编排。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        event_notifier: EventNotifier | None = None,
        terminal_callback: Callable[[str, RunStatus], Awaitable[None]] | None = None,
    ) -> None:
        """初始化 RunService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            event_notifier: 事件通知器，默认 Noop（切片 9，SSE 降延迟用）。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()
        self._terminal_callback = terminal_callback

    async def create_run(
        self,
        actor: ActorContext,
        project_id: str,
        run_type: str,
        input_payload: dict,
        correlation_id: str,
    ) -> Run:
        """创建 Run 并写入首个 run_created 事件。

        参数:
            actor: 当前请求的可信用户上下文。
            project_id: 所属 Project 标识符。
            run_type: Run 类型。
            input_payload: 输入数据。
            correlation_id: 关联标识符。

        返回:
            创建后的 Run，event_sequence 已推进到 2。
        """
        run = create_run(project_id, actor.owner_id, run_type, input_payload)
        created_event = create_event(
            run_id=run.run_id,
            sequence=1,
            event_type="run_created",
            actor_type="user",
            correlation_id=correlation_id,
            payload={"status": run.status.value},
        )
        updated_run = Run(
            run_id=run.run_id,
            project_id=run.project_id,
            owner_id=run.owner_id,
            run_type=run.run_type,
            status=run.status,
            input_payload=run.input_payload,
            result_payload=run.result_payload,
            event_sequence=2,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            event_repo = self._event_repo_factory(session)
            await run_repo.add(updated_run)
            await session.flush()
            await event_repo.add(created_event)
            await session.commit()
        await notify_run_event(self._event_notifier, updated_run.run_id)
        return updated_run

    async def get_run(self, actor: ActorContext, run_id: str) -> Run:
        """获取当前 actor 可见的单个 Run。

        异常:
            RunNotFoundError: Run 不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(run_id)
            if run is None or run.owner_id != actor.owner_id:
                raise RunNotFoundError(run_id)
            return run

    async def list_events(
        self,
        actor: ActorContext,
        run_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list:
        """列出当前 actor 可见 Run 的 Event，支持 sequence 游标分页。

        参数:
            actor: 当前请求的可信用户上下文。
            run_id: 目标 Run 标识符。
            after_sequence: 只返回 sequence 大于该值的事件（断线重放游标）。
            limit: 单次返回的最大条数。
        """
        await self.get_run(actor, run_id)
        async with self._session_factory() as session:
            event_repo = self._event_repo_factory(session)
            return await event_repo.list_after(run_id, after_sequence, limit)

    async def start_run(
        self,
        actor: ActorContext,
        run_id: str,
        correlation_id: str,
    ) -> Run:
        """将 Run 从 QUEUED 转换到 RUNNING。"""
        return await self._transition(
            actor=actor,
            run_id=run_id,
            expected_status=RunStatus.QUEUED,
            target_status=RunStatus.RUNNING,
            event_type="run_started",
            actor_type="system",
            correlation_id=correlation_id,
            payload={},
        )

    async def complete_run(
        self,
        actor: ActorContext,
        run_id: str,
        result_payload: dict,
        correlation_id: str,
    ) -> Run:
        """将 Run 从 RUNNING 转换到 SUCCEEDED 并记录结果。"""
        return await self._transition(
            actor=actor,
            run_id=run_id,
            expected_status=RunStatus.RUNNING,
            target_status=RunStatus.SUCCEEDED,
            event_type="run_completed",
            actor_type="system",
            correlation_id=correlation_id,
            payload={"result": result_payload},
            result_payload=result_payload,
        )

    async def fail_run(
        self,
        actor: ActorContext,
        run_id: str,
        error_payload: dict,
        correlation_id: str,
    ) -> Run:
        """将 Run 从 RUNNING 转换到 FAILED 并记录错误。"""
        return await self._transition(
            actor=actor,
            run_id=run_id,
            expected_status=RunStatus.RUNNING,
            target_status=RunStatus.FAILED,
            event_type="run_failed",
            actor_type="system",
            correlation_id=correlation_id,
            payload={"error": error_payload},
        )

    async def cancel_run(
        self,
        actor: ActorContext,
        run_id: str,
        correlation_id: str,
    ) -> Run:
        """取消 Run。

        QUEUED/RETRY_WAIT 直接转为 CANCELLED；
        RUNNING 转为 CANCEL_REQUESTED；
        CANCEL_REQUESTED 转为 CANCELLED。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(run_id, actor.owner_id)
            if run is None:
                raise RunNotFoundError(run_id)

            if run.status in {
                RunStatus.CANCEL_REQUESTED,
                RunStatus.QUEUED,
                RunStatus.RETRY_WAIT,
                RunStatus.WAITING_INPUT,
                RunStatus.WAITING_DEPENDENCY,
            }:
                target_status = RunStatus.CANCELLED
            elif run.status == RunStatus.RUNNING:
                target_status = RunStatus.CANCEL_REQUESTED
            else:
                # 终态或无法取消的状态，按非法转换处理
                new_run = run.transition_to(RunStatus.CANCELLED)
                # transition_to 会抛出异常，这里只是为了类型检查
                return new_run

            result = await self._execute_transition(
                session=session,
                run=run,
                run_repo=run_repo,
                event_repo=self._event_repo_factory(session),
                target_status=target_status,
                event_type=(
                    "run_cancel_requested"
                    if target_status == RunStatus.CANCEL_REQUESTED
                    else "run_cancelled"
                ),
                actor_type="user",
                correlation_id=correlation_id,
                payload={},
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run_id)
        await self._notify_terminal(run_id, target_status)
        return result

    async def _transition(
        self,
        actor: ActorContext,
        run_id: str,
        expected_status: RunStatus,
        target_status: RunStatus,
        event_type: str,
        actor_type: str,
        correlation_id: str,
        payload: dict,
        result_payload: dict | None = None,
    ) -> Run:
        """通用状态转换。

        获取行锁后先校验当前状态是否与预期一致；不一致说明并发修改，
        抛出 ``RunConcurrentModificationError`` 而非 ``InvalidRunTransitionError``。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            event_repo = self._event_repo_factory(session)
            run = await run_repo.get_by_id_for_update(run_id, actor.owner_id)
            if run is None:
                raise RunNotFoundError(run_id)
            if run.status != expected_status:
                raise RunConcurrentModificationError(run_id)
            result = await self._execute_transition(
                session=session,
                run=run,
                run_repo=run_repo,
                event_repo=event_repo,
                target_status=target_status,
                event_type=event_type,
                actor_type=actor_type,
                correlation_id=correlation_id,
                payload=payload,
                result_payload=result_payload,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run_id)
        await self._notify_terminal(run_id, target_status)
        return result

    async def _notify_terminal(self, run_id: str, status: RunStatus) -> None:
        """业务提交后 best-effort 通知扩展聚合收敛终态引用。"""
        if (
            status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
            or self._terminal_callback is None
        ):
            return
        try:
            await self._terminal_callback(run_id, status)
        except Exception as exc:
            logger.warning(
                "Run 终态回调失败: run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )

    async def _execute_transition(
        self,
        session: Session,
        run: Run,
        run_repo: RunRepository,
        event_repo: EventRepository,
        target_status: RunStatus,
        event_type: str,
        actor_type: str,
        correlation_id: str,
        payload: dict,
        result_payload: dict | None = None,
    ) -> Run:
        """在已获取锁的事务内执行状态转换和事件写入。"""
        new_run = run.transition_to(target_status)
        if result_payload is not None:
            new_run = Run(
                run_id=new_run.run_id,
                project_id=new_run.project_id,
                owner_id=new_run.owner_id,
                run_type=new_run.run_type,
                status=new_run.status,
                input_payload=new_run.input_payload,
                result_payload=result_payload,
                event_sequence=new_run.event_sequence,
                created_at=new_run.created_at,
                updated_at=new_run.updated_at,
            )
        success = await run_repo.update_status(
            run_id=run.run_id,
            expected_status=run.status,
            new_status=new_run.status,
            new_event_sequence=new_run.event_sequence + 1,
        )
        if not success:
            raise RunConcurrentModificationError(run.run_id)
        event = create_event(
            run_id=run.run_id,
            sequence=run.event_sequence,
            event_type=event_type,
            actor_type=actor_type,
            correlation_id=correlation_id,
            payload=payload,
        )
        await event_repo.add(event)
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
