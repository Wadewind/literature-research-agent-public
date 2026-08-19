"""基于 Valkey Pub/Sub 的 EventNotifier 适配器。

Channel 命名为 ``run-events:{run_id}``，消息体只携带 ``run_id``。
Pub/Sub 不保证投递，仅用于降低 SSE 延迟；正确性由 PostgreSQL
轮询兜底保证。
"""

import logging
from collections.abc import AsyncIterator

from redis.asyncio import Redis

from literature_agent.application.ports.event_notifier import EventNotifier

logger = logging.getLogger(__name__)


def run_events_channel(run_id: str) -> str:
    """返回某个 Run 的通知 channel 名。"""
    return f"run-events:{run_id}"


class ValkeyEventNotifier(EventNotifier):
    """把 Run 事件通知发布到 Valkey Pub/Sub 的适配器。

    连接在首次使用时惰性建立，API 启动不强依赖 Valkey 可用。
    """

    def __init__(self, redis_url: str) -> None:
        """初始化适配器。

        参数:
            redis_url: Valkey/Redis 连接串。
        """
        self._redis = Redis.from_url(redis_url, decode_responses=True)

    async def notify(self, run_id: str) -> None:
        """发布一条只含 run_id 的通知。"""
        await self._redis.publish(run_events_channel(run_id), run_id)

    def subscribe(self, run_id: str) -> AsyncIterator[None]:
        """订阅某个 Run 的通知流。"""
        return self._listen(run_id)

    async def _listen(self, run_id: str) -> AsyncIterator[None]:
        """在独立 Pub/Sub 连接上监听 channel，每收到一条消息迭代一次。"""
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(run_events_channel(run_id))
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=None,
                )
                if message is not None:
                    yield None
        finally:
            try:
                await pubsub.unsubscribe(run_events_channel(run_id))
                await pubsub.aclose()
            except Exception:
                logger.warning("关闭事件订阅失败: run_id=%s", run_id, exc_info=True)

    async def aclose(self) -> None:
        """关闭连接池。"""
        await self._redis.aclose()
