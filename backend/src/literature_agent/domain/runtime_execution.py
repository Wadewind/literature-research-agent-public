"""Research Agent Runtime Execution 的持久控制事实。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class RuntimeControlState(StrEnum):
    """平台侧 Runtime Execution 状态，不映射 SDK 枚举。"""

    RUNNING = "running"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeExecutionError(ValueError):
    """Runtime Execution 领域不变量被违反。"""


@dataclass(frozen=True, slots=True)
class RuntimeExecutionPermit:
    """一次 Runtime lease 的不可伪造平台许可。"""

    turn_run_id: str
    owner_id: str
    attempt_id: str
    fencing_token: int


@dataclass(frozen=True, slots=True)
class RuntimeExecution:
    """一个 AgentTurnRun 唯一对应的逻辑 Runtime Execution。"""

    turn_run_id: str
    session_id: str
    runtime_execution_id: str
    request_hash: str
    runtime_revision: str
    graph_revision: str
    deepagents_version: str
    langgraph_version: str
    state: RuntimeControlState
    fencing_token: int
    current_attempt_id: str | None
    lease_owner_id: str | None
    lease_expires_at: datetime | None
    last_checkpoint_id: str | None
    last_error_kind: str | None
    last_error_code: str | None
    last_safe_message: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None

    @property
    def permit(self) -> RuntimeExecutionPermit:
        """返回当前 lease 许可；没有 owner 时拒绝构造。"""
        if self.lease_owner_id is None or self.current_attempt_id is None:
            raise RuntimeExecutionError("Runtime Execution 当前没有有效 lease")
        return RuntimeExecutionPermit(
            self.turn_run_id,
            self.lease_owner_id,
            self.current_attempt_id,
            self.fencing_token,
        )

    def is_orphaned(self, now: datetime) -> bool:
        """RUNNING 且没有有效 lease 即为 orphan。"""
        return self.state is RuntimeControlState.RUNNING and (
            self.lease_owner_id is None
            or self.current_attempt_id is None
            or self.lease_expires_at is None
            or self.lease_expires_at <= now
        )

    def require_compatible(
        self,
        *,
        session_id: str,
        runtime_execution_id: str,
        request_hash: str,
        runtime_revision: str,
        graph_revision: str,
        deepagents_version: str,
        langgraph_version: str,
    ) -> None:
        """恢复只接受完全一致的请求与 Runtime/Graph revision。"""
        if (
            self.session_id != session_id
            or self.runtime_execution_id != runtime_execution_id
        ):
            raise RuntimeExecutionError("Runtime Execution 身份冲突")
        if self.request_hash != request_hash:
            raise RuntimeExecutionError("Runtime Execution 请求哈希冲突")
        actual = (
            self.runtime_revision,
            self.graph_revision,
            self.deepagents_version,
            self.langgraph_version,
        )
        expected = (
            runtime_revision,
            graph_revision,
            deepagents_version,
            langgraph_version,
        )
        if actual != expected:
            raise RuntimeExecutionError("Runtime Execution 版本不兼容")

    def claim(
        self,
        *,
        attempt_id: str,
        lease_owner_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> RuntimeExecution:
        """认领无 owner/过期 lease；重复 owner 认领保持同一 fencing token。"""
        self._require_running()
        if not attempt_id or not lease_owner_id:
            raise RuntimeExecutionError("Runtime lease owner/Attempt 不能为空")
        if lease_expires_at <= now:
            raise RuntimeExecutionError("Runtime lease 必须晚于当前时间")
        if (
            self.lease_owner_id == lease_owner_id
            and self.current_attempt_id == attempt_id
            and self.lease_expires_at is not None
            and self.lease_expires_at > now
        ):
            return replace(self, lease_expires_at=lease_expires_at, updated_at=now)
        if not self.is_orphaned(now):
            raise RuntimeExecutionError("Runtime Execution 仍有有效 owner")
        return replace(
            self,
            current_attempt_id=attempt_id,
            lease_owner_id=lease_owner_id,
            lease_expires_at=lease_expires_at,
            fencing_token=self.fencing_token + 1,
            updated_at=now,
        )

    def renew(
        self,
        permit: RuntimeExecutionPermit,
        *,
        lease_expires_at: datetime,
        now: datetime,
    ) -> RuntimeExecution:
        """当前 owner 续租，fencing token 不变。"""
        self._require_permit(permit)
        if lease_expires_at <= now:
            raise RuntimeExecutionError("Runtime lease 必须晚于当前时间")
        return replace(self, lease_expires_at=lease_expires_at, updated_at=now)

    def validate_permit(self, permit: RuntimeExecutionPermit, *, now: datetime) -> None:
        """校验 owner/fence 以及 lease 尚未过期。"""
        self._require_permit(permit)
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise RuntimeExecutionError("Runtime lease 已过期")

    def record_checkpoint(
        self,
        *,
        owner_id: str,
        attempt_id: str,
        fencing_token: int,
        checkpoint_id: str,
        now: datetime,
    ) -> RuntimeExecution:
        """当前 owner 条件推进最后确认的 Checkpoint。"""
        self._require_permit(
            RuntimeExecutionPermit(self.turn_run_id, owner_id, attempt_id, fencing_token)
        )
        if not checkpoint_id:
            raise RuntimeExecutionError("Runtime checkpoint_id 不能为空")
        return replace(self, last_checkpoint_id=checkpoint_id, updated_at=now)

    def succeed(
        self,
        *,
        owner_id: str,
        attempt_id: str,
        fencing_token: int,
        checkpoint_id: str,
        now: datetime,
    ) -> RuntimeExecution:
        """当前 owner 以已确认 Checkpoint 原子形成成功终态。"""
        updated = self.record_checkpoint(
            owner_id=owner_id,
            attempt_id=attempt_id,
            fencing_token=fencing_token,
            checkpoint_id=checkpoint_id,
            now=now,
        )
        return updated._finish(RuntimeControlState.SUCCEEDED, now)

    def fail(
        self,
        permit: RuntimeExecutionPermit,
        *,
        error_code: str,
        safe_message: str,
        now: datetime,
    ) -> RuntimeExecution:
        """当前 owner 持久化不可恢复失败。"""
        self._require_permit(permit)
        return replace(
            self,
            last_error_kind="permanent",
            last_error_code=_bounded(error_code, 100, "error_code"),
            last_safe_message=_bounded(safe_message, 500, "safe_message"),
        )._finish(RuntimeControlState.FAILED, now)

    def record_temporary_error(
        self,
        *,
        owner_id: str,
        attempt_id: str,
        fencing_token: int,
        error_code: str,
        safe_message: str,
        now: datetime,
    ) -> RuntimeExecution:
        """记录安全临时错误并释放 lease，保留相同 Execution 可恢复。"""
        self._require_permit(
            RuntimeExecutionPermit(self.turn_run_id, owner_id, attempt_id, fencing_token)
        )
        return replace(
            self,
            current_attempt_id=None,
            lease_owner_id=None,
            lease_expires_at=None,
            last_error_kind="temporary",
            last_error_code=_bounded(error_code, 100, "error_code"),
            last_safe_message=_bounded(safe_message, 500, "safe_message"),
            updated_at=now,
        )

    def cancel(self, *, now: datetime) -> RuntimeExecution:
        """由已验证的业务取消/恢复协调层形成取消终态。"""
        self._require_running()
        return self._finish(RuntimeControlState.CANCELLED, now)

    def _finish(self, state: RuntimeControlState, now: datetime) -> RuntimeExecution:
        self._require_running()
        return replace(
            self,
            state=state,
            current_attempt_id=None,
            lease_owner_id=None,
            lease_expires_at=None,
            updated_at=now,
            finished_at=now,
        )

    def _require_running(self) -> None:
        if self.state is not RuntimeControlState.RUNNING:
            raise RuntimeExecutionError("Runtime Execution 终态不可重写")

    def _require_permit(self, permit: RuntimeExecutionPermit) -> None:
        self._require_running()
        if (
            permit.turn_run_id != self.turn_run_id
            or permit.owner_id != self.lease_owner_id
            or permit.attempt_id != self.current_attempt_id
            or permit.fencing_token != self.fencing_token
        ):
            raise RuntimeExecutionError("Runtime lease fencing 校验失败")


def create_runtime_execution(
    *,
    turn_run_id: str,
    session_id: str,
    runtime_execution_id: str,
    request_hash: str,
    runtime_revision: str,
    graph_revision: str,
    deepagents_version: str,
    langgraph_version: str,
    attempt_id: str,
    lease_owner_id: str,
    lease_expires_at: datetime,
    now: datetime,
) -> RuntimeExecution:
    """创建首次由当前 Attempt 持有、fencing token 为 1 的 Execution。"""
    for name, value in {
        "turn_run_id": turn_run_id,
        "session_id": session_id,
        "runtime_execution_id": runtime_execution_id,
        "runtime_revision": runtime_revision,
        "graph_revision": graph_revision,
        "deepagents_version": deepagents_version,
        "langgraph_version": langgraph_version,
        "attempt_id": attempt_id,
        "lease_owner_id": lease_owner_id,
    }.items():
        _bounded(value, 255, name)
    if len(request_hash) != 64 or any(ch not in "0123456789abcdef" for ch in request_hash):
        raise RuntimeExecutionError("request_hash 必须是小写 SHA-256")
    if lease_expires_at <= now:
        raise RuntimeExecutionError("Runtime lease 必须晚于当前时间")
    return RuntimeExecution(
        turn_run_id=turn_run_id,
        session_id=session_id,
        runtime_execution_id=runtime_execution_id,
        request_hash=request_hash,
        runtime_revision=runtime_revision,
        graph_revision=graph_revision,
        deepagents_version=deepagents_version,
        langgraph_version=langgraph_version,
        state=RuntimeControlState.RUNNING,
        fencing_token=1,
        current_attempt_id=attempt_id,
        lease_owner_id=lease_owner_id,
        lease_expires_at=lease_expires_at,
        last_checkpoint_id=None,
        last_error_kind=None,
        last_error_code=None,
        last_safe_message=None,
        started_at=now,
        updated_at=now,
    )


def _bounded(value: str, maximum: int, name: str) -> str:
    if not value or len(value) > maximum:
        raise RuntimeExecutionError(f"{name} 必须是 1..{maximum} 个字符")
    return value
