"""Run Event 领域值对象。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Event:
    """Run 内发生的一个业务事件。

    Event 是产品历史和前端事件的时间线来源，Payload 只保存必要摘要，
    不保存完整 Prompt、论文全文或敏感参数。

    属性:
        event_id: 事件标识符。
        event_version: 事件 schema 版本。
        event_type: 事件类型。
        run_id: 所属 Run 标识符。
        sequence: Run 内严格递增的序列号。
        occurred_at: 发生时间（UTC）。
        actor_type: 触发者类型，例如 ``user`` 或 ``system``。
        correlation_id: 用于关联请求/Trace 的标识符。
        payload: 小型结构化负载。
    """

    event_id: str
    event_version: str
    event_type: str
    run_id: str
    sequence: int
    occurred_at: datetime
    actor_type: str
    correlation_id: str
    payload: dict


def create_event(
    run_id: str,
    sequence: int,
    event_type: str,
    actor_type: str,
    correlation_id: str,
    payload: dict | None = None,
) -> Event:
    """创建新的 Event 值对象。

    参数:
        run_id: 所属 Run 标识符。
        sequence: Run 内序列号。
        event_type: 事件类型。
        actor_type: 触发者类型。
        correlation_id: 关联标识符。
        payload: 可选的事件负载。

    返回:
        新的 ``Event`` 实例。
    """
    return Event(
        event_id=str(uuid4()),
        event_version="1.0",
        event_type=event_type,
        run_id=run_id,
        sequence=sequence,
        occurred_at=datetime.now(UTC),
        actor_type=actor_type,
        correlation_id=correlation_id,
        payload=payload or {},
    )
