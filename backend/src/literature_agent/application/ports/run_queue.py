"""Run Queue 端口。"""

from typing import Protocol


class RunQueue(Protocol):
    """Run 执行队列的抽象端口。

    只负责把稳定 ``run_id`` 投递给后台 Worker；业务状态始终以
    PostgreSQL 为准，队列不作事实来源。
    """

    async def enqueue_run(self, run_id: str) -> None:
        """投递一个只携带 ``run_id`` 的执行 Job。

        正常返回仅表示新 Job 已创建，或相同稳定 ID 的 ``queued``、
        ``deferred``、``in_progress`` Job 已存在。实现应保证重复投递同一
        ``run_id`` 是安全的（去重或幂等）；无法确认存在可执行 Job 时必须
        抛出异常，调用方不能把模糊结果当作投递成功。

        异常:
            Exception: 队列不可用或无法确认投递等临时基础设施错误，
                由调用方负责重试。
        """
        ...
