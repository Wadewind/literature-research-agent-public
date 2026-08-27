"""Research Agent Runtime Execution lease、fencing 与终态协调。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.runtime_execution_repository import (
    RuntimeExecutionRepository,
)
from literature_agent.application.ports.session import Session
from literature_agent.domain.run import RunStatus
from literature_agent.domain.run_attempt import AttemptStatus
from literature_agent.domain.runtime_execution import (
    RuntimeControlState,
    RuntimeExecution,
    RuntimeExecutionError,
    RuntimeExecutionPermit,
    create_runtime_execution,
)

RUNTIME_CONTRACT_REVISION = "research-agent-runtime.v1"
RUNTIME_GRAPH_REVISION = "deep-agent-graph.v4"
DEEPAGENTS_REVISION = "0.7.8"
LANGGRAPH_REVISION = "1.2.11"


class RuntimeExecutionControlError(Exception):
    """只携带稳定 code、安全描述与可重试分类的控制错误。"""

    def __init__(self, code: str, safe_message: str, *, temporary: bool = False) -> None:
        self.code = code
        self.safe_message = safe_message
        self.temporary = temporary
        super().__init__(safe_message)


TSession = TypeVar("TSession", bound=Session)


class RuntimeExecutionControlService[TSession: Session]:
    """以短事务管理 Runtime Execution，所有模型/Tool/Checkpoint 调用均在其外。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        attempt_repo_factory: Callable[[TSession], AttemptRepository],
        execution_repo_factory: Callable[[TSession], RuntimeExecutionRepository],
        lease_seconds: float,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("Runtime lease_seconds 必须为正数")
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._attempt_repo_factory = attempt_repo_factory
        self._execution_repo_factory = execution_repo_factory
        self._lease_seconds = lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def claim(
        self,
        *,
        turn_run_id: str,
        session_id: str,
        runtime_execution_id: str,
        request_hash: str,
        owner_id: str,
    ) -> RuntimeExecution:
        """为当前最新 RUNNING Attempt 创建或认领同一逻辑 Execution。"""
        now = self._clock()
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(turn_run_id)
            if run is None:
                raise RuntimeExecutionControlError(
                    "runtime_turn_not_found", "业务 Turn 不存在"
                )
            locked = await run_repo.get_by_id_for_update(turn_run_id, run.owner_id)
            if locked is None:
                raise RuntimeExecutionControlError(
                    "runtime_turn_not_found", "业务 Turn 不存在"
                )
            if locked.status is RunStatus.CANCEL_REQUESTED:
                raise RuntimeExecutionControlError(
                    "runtime_turn_cancelled", "业务 Turn 已请求取消"
                )
            if locked.status is not RunStatus.RUNNING:
                raise RuntimeExecutionControlError(
                    "runtime_turn_not_running",
                    "业务 Turn 当前不可执行",
                    temporary=locked.status is RunStatus.RETRY_WAIT,
                )
            attempt = await self._attempt_repo_factory(session).get_latest_by_run(turn_run_id)
            if attempt is None or attempt.status is not AttemptStatus.RUNNING:
                raise RuntimeExecutionControlError(
                    "runtime_attempt_not_running", "当前没有可绑定的 RUNNING Attempt"
                )
            repo = self._execution_repo_factory(session)
            execution = await repo.get_for_update(turn_run_id)
            lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            if execution is None:
                proposed = create_runtime_execution(
                    turn_run_id=turn_run_id,
                    session_id=session_id,
                    runtime_execution_id=runtime_execution_id,
                    request_hash=request_hash,
                    runtime_revision=RUNTIME_CONTRACT_REVISION,
                    graph_revision=RUNTIME_GRAPH_REVISION,
                    deepagents_version=DEEPAGENTS_REVISION,
                    langgraph_version=LANGGRAPH_REVISION,
                    attempt_id=attempt.attempt_id,
                    lease_owner_id=owner_id,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
                if await repo.add_if_absent(proposed):
                    await session.commit()
                    return proposed
                execution = await repo.get_for_update(turn_run_id)
                if execution is None:
                    raise RuntimeExecutionControlError(
                        "runtime_execution_conflict",
                        "Runtime Execution 并发创建失败",
                        temporary=True,
                    )
            try:
                execution.require_compatible(
                    session_id=session_id,
                    runtime_execution_id=runtime_execution_id,
                    request_hash=request_hash,
                    runtime_revision=RUNTIME_CONTRACT_REVISION,
                    graph_revision=RUNTIME_GRAPH_REVISION,
                    deepagents_version=DEEPAGENTS_REVISION,
                    langgraph_version=LANGGRAPH_REVISION,
                )
                claimed = execution.claim(
                    attempt_id=attempt.attempt_id,
                    lease_owner_id=owner_id,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
            except RuntimeExecutionError as exc:
                code = (
                    "runtime_version_incompatible"
                    if "版本不兼容" in str(exc)
                    else "runtime_execution_leased"
                    if "有效 owner" in str(exc)
                    else "runtime_execution_conflict"
                )
                raise RuntimeExecutionControlError(
                    code,
                    "Runtime 版本不兼容"
                    if code == "runtime_version_incompatible"
                    else "Runtime Execution lease 当前不可认领",
                    temporary=code == "runtime_execution_leased",
                ) from exc
            if not await repo.save(claimed, expected=execution):
                raise RuntimeExecutionControlError(
                    "runtime_execution_conflict",
                    "Runtime Execution 认领发生并发冲突",
                    temporary=True,
                )
            await session.commit()
            return claimed

    async def get(self, turn_run_id: str) -> RuntimeExecution | None:
        """读取持久 Runtime 状态。"""
        async with self._session_factory() as session:
            return await self._execution_repo_factory(session).get(turn_run_id)

    async def can_recover(self, turn_run_id: str) -> bool:
        """只有当前业务 Attempt 可运行且 Execution 已 orphan 时允许恢复。"""
        now = self._clock()
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(turn_run_id)
            attempt = await self._attempt_repo_factory(session).get_latest_by_run(turn_run_id)
            execution = await self._execution_repo_factory(session).get(turn_run_id)
            return bool(
                run is not None
                and run.status is RunStatus.RUNNING
                and attempt is not None
                and attempt.status is AttemptStatus.RUNNING
                and execution is not None
                and execution.is_orphaned(now)
            )

    async def assert_active(self, permit: RuntimeExecutionPermit) -> RuntimeExecution:
        """模型、Tool 与结果提交边界重新检查业务状态、Attempt 和 fence。"""
        now = self._clock()
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(permit.turn_run_id)
            attempt = await self._attempt_repo_factory(session).get_latest_by_run(
                permit.turn_run_id
            )
            execution = await self._execution_repo_factory(session).get(permit.turn_run_id)
            if (
                run is None
                or run.status is not RunStatus.RUNNING
                or attempt is None
                or attempt.attempt_id != permit.attempt_id
                or attempt.status is not AttemptStatus.RUNNING
                or execution is None
            ):
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost", "Runtime Execution lease 已失效"
                )
            try:
                execution.validate_permit(permit, now=now)
            except RuntimeExecutionError as exc:
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost", "Runtime Execution lease 已失效"
                ) from exc
            return execution

    async def renew(self, permit: RuntimeExecutionPermit) -> RuntimeExecution:
        """当前 owner 在短事务中续租；Attempt/Run 失效时拒绝。"""
        return await self._mutate_owned(
            permit,
            lambda item, now: item.renew(
                permit,
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                now=now,
            ),
        )

    async def record_checkpoint(
        self, permit: RuntimeExecutionPermit, checkpoint_id: str
    ) -> RuntimeExecution:
        """当前 fence 条件推进最后确认 checkpoint。"""
        return await self._mutate_owned(
            permit,
            lambda item, now: item.record_checkpoint(
                owner_id=permit.owner_id,
                attempt_id=permit.attempt_id,
                fencing_token=permit.fencing_token,
                checkpoint_id=checkpoint_id,
                now=now,
            ),
        )

    async def succeed(
        self, permit: RuntimeExecutionPermit, checkpoint_id: str
    ) -> RuntimeExecution:
        """当前 fence 条件形成成功终态。"""
        return await self._mutate_owned(
            permit,
            lambda item, now: item.succeed(
                owner_id=permit.owner_id,
                attempt_id=permit.attempt_id,
                fencing_token=permit.fencing_token,
                checkpoint_id=checkpoint_id,
                now=now,
            ),
        )

    async def temporary_error(
        self, permit: RuntimeExecutionPermit, *, code: str, safe_message: str
    ) -> RuntimeExecution:
        """释放 lease 并保留可恢复 RUNNING Execution。"""
        return await self._mutate_owned(
            permit,
            lambda item, now: item.record_temporary_error(
                owner_id=permit.owner_id,
                attempt_id=permit.attempt_id,
                fencing_token=permit.fencing_token,
                error_code=code,
                safe_message=safe_message,
                now=now,
            ),
        )

    async def fail(
        self, permit: RuntimeExecutionPermit, *, code: str, safe_message: str
    ) -> RuntimeExecution:
        """当前 fence 条件形成永久失败终态。"""
        return await self._mutate_owned(
            permit,
            lambda item, now: item.fail(
                permit, error_code=code, safe_message=safe_message, now=now
            ),
        )

    async def cancel_for_business(self, turn_run_id: str) -> RuntimeExecution | None:
        """业务已进入取消路径时，以行锁幂等形成 Runtime CANCELLED。"""
        now = self._clock()
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id(turn_run_id)
            if run is None:
                raise RuntimeExecutionControlError(
                    "runtime_turn_not_found", "业务 Turn 不存在"
                )
            locked = await run_repo.get_by_id_for_update(turn_run_id, run.owner_id)
            if locked is None:
                raise RuntimeExecutionControlError(
                    "runtime_turn_not_found", "业务 Turn 不存在"
                )
            if locked.status not in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLED}:
                raise RuntimeExecutionControlError(
                    "runtime_cancel_not_authorized", "业务 Turn 尚未请求取消"
                )
            repo = self._execution_repo_factory(session)
            execution = await repo.get_for_update(turn_run_id)
            if execution is None or execution.state is RuntimeControlState.CANCELLED:
                return execution
            if execution.state is not RuntimeControlState.RUNNING:
                return execution
            cancelled = execution.cancel(now=now)
            if not await repo.save(cancelled, expected=execution):
                raise RuntimeExecutionControlError(
                    "runtime_execution_conflict",
                    "Runtime Execution 取消发生并发冲突",
                    temporary=True,
                )
            await session.commit()
            return cancelled

    async def _mutate_owned(
        self,
        permit: RuntimeExecutionPermit,
        mutate: Callable[[RuntimeExecution, datetime], RuntimeExecution],
        *,
        require_active: bool = True,
    ) -> RuntimeExecution:
        if require_active:
            await self.assert_active(permit)
        now = self._clock()
        async with self._session_factory() as session:
            if require_active:
                run_repo = self._run_repo_factory(session)
                run = await run_repo.get_by_id(permit.turn_run_id)
                locked = (
                    await run_repo.get_by_id_for_update(permit.turn_run_id, run.owner_id)
                    if run is not None
                    else None
                )
                attempt = await self._attempt_repo_factory(session).get_latest_by_run(
                    permit.turn_run_id
                )
                if (
                    locked is None
                    or locked.status is not RunStatus.RUNNING
                    or attempt is None
                    or attempt.attempt_id != permit.attempt_id
                    or attempt.status is not AttemptStatus.RUNNING
                ):
                    raise RuntimeExecutionControlError(
                        "runtime_execution_lease_lost",
                        "Runtime Execution lease 已失效",
                    )
            repo = self._execution_repo_factory(session)
            execution = await repo.get_for_update(permit.turn_run_id)
            if execution is None:
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost", "Runtime Execution lease 已失效"
                )
            try:
                execution.validate_permit(permit, now=now)
                updated = mutate(execution, now)
            except RuntimeExecutionError as exc:
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost", "Runtime Execution lease 已失效"
                ) from exc
            if not await repo.save(updated, expected=execution):
                raise RuntimeExecutionControlError(
                    "runtime_execution_lease_lost", "Runtime Execution lease 已失效"
                )
            await session.commit()
            return updated
