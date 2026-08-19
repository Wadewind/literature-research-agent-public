"""Queue Outbox 领域实体。

Outbox 记录数据库提交与队列投递之间的持久化间隙：
创建 Run 的同一事务写入 Outbox 记录，后台派发器据此向 ARQ 投递 Job。
派发器崩溃或投递失败不会丢失任务，重复投递由 ARQ Job ID 去重和
Worker 端幂等执行兜底，实现业务上的 Effectively Once。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

# 派发失败退避基数（秒）与上限（秒）
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 60.0


class OutboxStatus(StrEnum):
    """Outbox 记录的派发状态。"""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    FAILED = "failed"


def compute_dispatch_backoff(attempt_count: int) -> timedelta:
    """根据已失败次数计算下一次派发尝试前的退避时长。

    采用指数退避并设置上限，避免临时故障期间密集重试。

    参数:
        attempt_count: 已发生的失败次数（从 1 开始）。

    返回:
        距离下一次尝试的等待时长。
    """
    seconds = min(_BACKOFF_BASE_SECONDS * (2 ** (attempt_count - 1)), _BACKOFF_MAX_SECONDS)
    return timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class QueueOutbox:
    """一条待投递的队列消息记录。

    属性:
        outbox_id: Outbox 记录标识符。
        run_id: 关联的 Run 标识符，一个 Run 最多一条 Outbox 记录。
        status: 当前派发状态。
        attempt_count: 已失败的投递尝试次数。
        scheduled_at: 下一次允许派发尝试的时间。
        dispatched_at: 成功投递时间，未投递时为 None。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    outbox_id: str
    run_id: str
    status: OutboxStatus
    attempt_count: int
    scheduled_at: datetime
    dispatched_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def mark_dispatched(self, dispatched_at: datetime) -> "QueueOutbox":
        """返回标记为已投递的新记录。

        参数:
            dispatched_at: 投递完成时间（UTC）。
        """
        return QueueOutbox(
            outbox_id=self.outbox_id,
            run_id=self.run_id,
            status=OutboxStatus.DISPATCHED,
            attempt_count=self.attempt_count,
            scheduled_at=self.scheduled_at,
            dispatched_at=dispatched_at,
            created_at=self.created_at,
            updated_at=dispatched_at,
        )

    def record_dispatch_failure(self, now: datetime, max_attempts: int) -> "QueueOutbox":
        """返回记录一次投递失败后的新记录。

        失败次数加一并按指数退避推迟下一次尝试；
        达到 ``max_attempts`` 后进入 ``FAILED`` 终态，等待人工介入。

        参数:
            now: 当前时间（UTC）。
            max_attempts: 允许的最大投递尝试次数。
        """
        attempt_count = self.attempt_count + 1
        exhausted = attempt_count >= max_attempts
        return QueueOutbox(
            outbox_id=self.outbox_id,
            run_id=self.run_id,
            status=OutboxStatus.FAILED if exhausted else OutboxStatus.PENDING,
            attempt_count=attempt_count,
            scheduled_at=now + compute_dispatch_backoff(attempt_count),
            dispatched_at=None,
            created_at=self.created_at,
            updated_at=now,
        )


def create_outbox_entry(run_id: str) -> QueueOutbox:
    """为 Run 创建一条待投递的 Outbox 记录。

    参数:
        run_id: 关联的 Run 标识符。

    返回:
        状态为 ``PENDING``、立即可派发的新记录。
    """
    now = datetime.now(UTC)
    return QueueOutbox(
        outbox_id=str(uuid4()),
        run_id=run_id,
        status=OutboxStatus.PENDING,
        attempt_count=0,
        scheduled_at=now,
        dispatched_at=None,
        created_at=now,
        updated_at=now,
    )
