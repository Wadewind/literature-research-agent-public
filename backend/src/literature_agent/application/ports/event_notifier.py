"""Event 通知端口。

Run 事件写入 PostgreSQL 后，通过本端口向订阅方（SSE 连接）发送
"有新事件"的轻量提示。通知只携带 ``run_id``，可以丢失：SSE 以
PostgreSQL 为事实来源，轮询兜底保证最终收敛。
"""

from collections.abc import AsyncIterator
from typing import Protocol


class EventNotifier(Protocol):
    """Run 事件通知的抽象端口。"""

    async def notify(self, run_id: str) -> None:
        """通知某个 Run 可能有新事件（payload 只有 run_id，可丢失）。"""
        ...

    def subscribe(self, run_id: str) -> AsyncIterator[None]:
        """订阅某个 Run 的通知，每收到一条产生一次迭代。

        实现为异步迭代器；不要求可靠投递，仅用于降低 SSE 延迟。
        """
        ...

    async def aclose(self) -> None:
        """释放底层连接资源。"""
        ...


class NoopEventNotifier(EventNotifier):
    """不发送任何通知的实现（测试与默认场景）。"""

    async def notify(self, run_id: str) -> None:
        """忽略通知。"""

    def subscribe(self, run_id: str) -> AsyncIterator[None]:
        """返回永不产生迭代的订阅。"""
        return _never()

    async def aclose(self) -> None:
        """无资源可释放。"""


async def _never() -> AsyncIterator[None]:
    """永不产出的事件流占位。"""
    return
    yield
