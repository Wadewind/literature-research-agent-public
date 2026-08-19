"""Run Attempt 领域实体。

Attempt 记录一次 Run 的具体执行尝试：哪个 Worker、何时开始、
最近一次心跳和最终结果。Attempt 是运维记录（lease/对账的依据），
业务事实仍以 Run 和 Event 为准。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class AttemptStatus(StrEnum):
    """Attempt 生命周期状态。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunAttempt:
    """一次 Run 执行尝试。

    属性:
        attempt_id: Attempt 标识符。
        run_id: 所属 Run 标识符。
        attempt_number: 尝试序号，Run 内从 1 开始唯一。
        worker_id: 执行该 Attempt 的 Worker 标识（主机名:进程号）。
        status: 当前状态。
        started_at: 开始时间（UTC）。
        heartbeat_at: 最近一次心跳时间（UTC）。
        finished_at: 结束时间，未结束为 None。
        error: 失败信息（类型与截断消息），未失败为 None。
    """

    attempt_id: str
    run_id: str
    attempt_number: int
    worker_id: str
    status: AttemptStatus
    started_at: datetime
    heartbeat_at: datetime
    finished_at: datetime | None = None
    error: dict | None = None

    def record_heartbeat(self, now: datetime) -> "RunAttempt":
        """返回更新心跳时间的新 Attempt。"""
        return RunAttempt(
            attempt_id=self.attempt_id,
            run_id=self.run_id,
            attempt_number=self.attempt_number,
            worker_id=self.worker_id,
            status=self.status,
            started_at=self.started_at,
            heartbeat_at=now,
            finished_at=self.finished_at,
            error=self.error,
        )

    def finish(
        self,
        status: AttemptStatus,
        now: datetime,
        error: dict | None = None,
    ) -> "RunAttempt":
        """返回以指定终态结束的新 Attempt。"""
        return RunAttempt(
            attempt_id=self.attempt_id,
            run_id=self.run_id,
            attempt_number=self.attempt_number,
            worker_id=self.worker_id,
            status=status,
            started_at=self.started_at,
            heartbeat_at=now,
            finished_at=now,
            error=error,
        )


def create_run_attempt(run_id: str, attempt_number: int, worker_id: str) -> RunAttempt:
    """创建状态为 ``RUNNING`` 的新 Attempt。

    参数:
        run_id: 所属 Run 标识符。
        attempt_number: 尝试序号（Run 内唯一，从 1 开始）。
        worker_id: 执行 Worker 标识。
    """
    now = datetime.now(UTC)
    return RunAttempt(
        attempt_id=str(uuid4()),
        run_id=run_id,
        attempt_number=attempt_number,
        worker_id=worker_id,
        status=AttemptStatus.RUNNING,
        started_at=now,
        heartbeat_at=now,
    )
