"""事件提交后的安全通知助手。

通知失败（Valkey 不可用等）只记日志，不影响业务结果；
调用方保证在事务提交之后调用，避免订阅方读到未提交状态。
"""

import logging

from literature_agent.application.ports.event_notifier import EventNotifier

logger = logging.getLogger(__name__)


async def notify_run_event(notifier: EventNotifier, run_id: str) -> None:
    """在事件事务提交后发送通知；失败静默降级为轮询收敛。"""
    try:
        await notifier.notify(run_id)
    except Exception:
        logger.warning("事件通知失败: run_id=%s", run_id, exc_info=True)
