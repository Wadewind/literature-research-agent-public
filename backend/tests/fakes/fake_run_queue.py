"""Run Queue 的内存假实现。"""


class FakeRunQueue:
    """记录已投递 run_id 的队列假实现，可配置为投递失败。"""

    def __init__(self, fail: bool = False) -> None:
        """初始化假队列。

        参数:
            fail: 为 True 时所有投递抛出异常，模拟队列不可用。
        """
        self._fail = fail
        self.enqueued_run_ids: list[str] = []

    async def enqueue_run(self, run_id: str) -> None:
        """记录一次投递，或按配置抛出异常。"""
        if self._fail:
            raise ConnectionError("队列不可用")
        self.enqueued_run_ids.append(run_id)
