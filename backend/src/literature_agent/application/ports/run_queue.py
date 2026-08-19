"""Run Queue 端口。"""

from typing import Protocol


class RunQueue(Protocol):
    """Run 执行队列的抽象端口。

    只负责把稳定 ``run_id`` 投递给后台 Worker；业务状态始终以
    PostgreSQL 为准，队列不作事实来源。
    """

    async def enqueue_run(self, run_id: str) -> None:
        """投递一个只携带 ``run_id`` 的执行 Job。

        实现应保证重复投递同一 ``run_id`` 是安全的（去重或幂等）。

        异常:
            Exception: 队列不可用等临时基础设施错误，由调用方负责重试。
        """
        ...
