"""等待 Run 的受约束正常恢复事务。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import StrEnum

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.event_notifier import (
    EventNotifier,
    NoopEventNotifier,
)
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    RunConcurrentModificationError,
    RunNotFoundError,
    RunSchedulingError,
)
from literature_agent.domain.run import Run, RunStatus


class ResumeReason(StrEnum):
    """允许触发正常恢复的业务原因。"""

    DEPENDENCY_COMPLETED = "dependency_wait_completed"
    HUMAN_INPUT_SUBMITTED = "human_input_submitted"


@dataclass(frozen=True, slots=True)
class _ResumePolicy:
    """恢复原因对应的状态和 Event 契约。"""

    expected_status: RunStatus
    event_type: str
    actor_type: str


_RESUME_POLICIES = {
    ResumeReason.DEPENDENCY_COMPLETED: _ResumePolicy(
        expected_status=RunStatus.WAITING_DEPENDENCY,
        event_type="dependency_wait_completed",
        actor_type="system",
    ),
    ResumeReason.HUMAN_INPUT_SUBMITTED: _ResumePolicy(
        expected_status=RunStatus.WAITING_INPUT,
        event_type="human_input_submitted",
        actor_type="user",
    ),
}


class WaitingRunResumeService[TSession: Session]:
    """原子完成等待 Run 重新排队、原因 Event 和 Outbox 重置。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        """初始化等待 Run 恢复服务。"""
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def resume(
        self,
        run_id: str,
        owner_id: str,
        project_id: str,
        reason: ResumeReason,
        correlation_id: str,
        payload: dict | None = None,
    ) -> Run:
        """按受限原因恢复等待 Run。

        Run ``WAITING_* → QUEUED``、原因 Event 和
        Outbox ``DISPATCHED → PENDING`` 在同一个短事务中提交。
        重复调用、错误等待状态或不可重置的 Outbox 均不会提交部分效果。
        """
        async with self._session_factory() as session:
            try:
                resumed = await self.resume_in_session(
                    session=session,
                    run_id=run_id,
                    owner_id=owner_id,
                    project_id=project_id,
                    reason=reason,
                    correlation_id=correlation_id,
                    payload=payload,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        await notify_run_event(self._event_notifier, run_id)
        return resumed

    async def resume_in_session(
        self,
        session: TSession,
        run_id: str,
        owner_id: str,
        project_id: str,
        reason: ResumeReason,
        correlation_id: str,
        payload: dict | None = None,
    ) -> Run:
        """在调用方事务内完成恢复三项原子子操作，但不提交事务。

        调用方负责在同一 ``session`` 中先保存依赖完成或 HumanInput 记录，
        再调用本方法并统一 commit/rollback；提交成功后的 Event 通知也由
        最外层应用服务负责。这样不会把前置业务记录和恢复拆成两个事务。
        """
        policy = _RESUME_POLICIES[reason]
        run_repo = self._run_repo_factory(session)
        run = await run_repo.get_by_id_for_update(run_id, owner_id)
        if run is None or run.project_id != project_id:
            raise RunNotFoundError(run_id)
        if run.status != policy.expected_status:
            raise RunConcurrentModificationError(run_id)

        queued = run.transition_to(RunStatus.QUEUED)
        updated = await run_repo.update_status(
            run_id=run.run_id,
            expected_status=policy.expected_status,
            new_status=RunStatus.QUEUED,
            new_event_sequence=run.event_sequence + 1,
        )
        if not updated:
            raise RunConcurrentModificationError(run_id)

        await self._event_repo_factory(session).add(
            create_event(
                run_id=run.run_id,
                sequence=run.event_sequence,
                event_type=policy.event_type,
                actor_type=policy.actor_type,
                correlation_id=correlation_id,
                payload=payload or {},
            )
        )
        scheduled = await self._outbox_repo_factory(session).schedule_again(run.run_id)
        if not scheduled:
            raise RunSchedulingError(run.run_id)
        return Run(
            run_id=queued.run_id,
            project_id=queued.project_id,
            owner_id=queued.owner_id,
            run_type=queued.run_type,
            status=queued.status,
            input_payload=queued.input_payload,
            result_payload=queued.result_payload,
            event_sequence=queued.event_sequence + 1,
            created_at=queued.created_at,
            updated_at=queued.updated_at,
        )
