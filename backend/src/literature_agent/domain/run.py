"""Run 领域实体与状态机。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from literature_agent.domain.exceptions import InvalidRunTransitionError


class RunType(StrEnum):
    """Run 类型枚举。

    Worker 按 ``run_type`` 显式分发到对应执行器；``RAG_ANSWER``
    在切片 8 接线，本阶段只定义。
    """

    INGESTION = "ingestion"
    INDEXING = "indexing"
    RAG_ANSWER = "rag_answer"


class RunStatus(StrEnum):
    """Run 生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.RETRY_WAIT,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.RETRY_WAIT: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.CANCEL_REQUESTED: {RunStatus.CANCELLED},
}

_FINAL_STATES: set[RunStatus] = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}

# 非终态状态：归档 Project 前必须不存在这些状态的 Run
ACTIVE_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RETRY_WAIT,
        RunStatus.CANCEL_REQUESTED,
    }
)


@dataclass(frozen=True, slots=True)
class Run:
    """后台执行业务 Run 的领域实体。

    Run 表示用户可查询、可取消的一次业务执行，拥有稳定状态、
    输入、结果和事件历史。

    属性:
        run_id: 稳定的 Run 标识符。
        project_id: 所属 Project 标识符。
        owner_id: 所有者标识符。
        run_type: Run 类型，例如 ``ingestion``。
        status: 当前状态。
        input_payload: 小型结构化输入。
        result_payload: 小型结构化结果。
        event_sequence: 下一个可用 Event sequence，从 1 开始。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    run_id: str
    project_id: str
    owner_id: str
    run_type: str
    status: RunStatus
    input_payload: dict
    result_payload: dict
    event_sequence: int
    created_at: datetime
    updated_at: datetime

    def transition_to(self, new_status: RunStatus) -> "Run":
        """执行状态转换并返回新的 Run 实体。

        参数:
            new_status: 目标状态。

        返回:
            状态更新后的新 ``Run`` 实例。

        异常:
            InvalidRunTransitionError: 当转换非法时抛出。
        """
        if self.status in _FINAL_STATES:
            raise InvalidRunTransitionError(self.run_id, self.status.value, new_status.value)
        allowed = _TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidRunTransitionError(self.run_id, self.status.value, new_status.value)
        now = datetime.now(UTC)
        return Run(
            run_id=self.run_id,
            project_id=self.project_id,
            owner_id=self.owner_id,
            run_type=self.run_type,
            status=new_status,
            input_payload=self.input_payload,
            result_payload=self.result_payload,
            event_sequence=self.event_sequence,
            created_at=self.created_at,
            updated_at=now,
        )


def create_run(
    project_id: str,
    owner_id: str,
    run_type: RunType | str,
    input_payload: dict | None = None,
) -> Run:
    """创建新的 Run 实体。

    参数:
        project_id: 所属 Project 标识符。
        owner_id: 所有者标识符。
        run_type: Run 类型，必须是 ``RunType`` 的合法取值。
        input_payload: 可选的输入数据。

    返回:
        状态为 ``QUEUED``、``event_sequence`` 为 1 的新 Run。

    异常:
        ValueError: ``run_type`` 不是合法的 ``RunType`` 取值。
    """
    now = datetime.now(UTC)
    return Run(
        run_id=str(uuid4()),
        project_id=project_id,
        owner_id=owner_id,
        run_type=RunType(run_type).value,
        status=RunStatus.QUEUED,
        input_payload=input_payload or {},
        result_payload={},
        event_sequence=1,
        created_at=now,
        updated_at=now,
    )
