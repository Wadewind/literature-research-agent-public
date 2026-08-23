"""事件提交后的安全通知助手。

通知失败（Valkey 不可用等）只记日志，不影响业务结果；
调用方保证在事务提交之后调用，避免订阅方读到未提交状态。
"""

import logging

from literature_agent.application.ports.event_notifier import EventNotifier
from literature_agent.observability import log_event

logger = logging.getLogger(__name__)


async def notify_run_event(notifier: EventNotifier, run_id: str) -> None:
    """在事件事务提交后发送通知；失败静默降级为轮询收敛。"""
    try:
        await notifier.notify(run_id)
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "event_notification_failed",
            exc=exc,
            run_id=run_id,
            error_code=type(exc).__name__,
        )
