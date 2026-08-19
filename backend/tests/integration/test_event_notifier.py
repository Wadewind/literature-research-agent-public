"""Valkey Pub/Sub EventNotifier 集成测试。"""

import asyncio

import pytest_asyncio
from testcontainers.community.redis import RedisContainer

from literature_agent.infrastructure.queue.valkey_event_notifier import (
    ValkeyEventNotifier,
)


@pytest_asyncio.fixture
async def valkey_url():
    """启动 Testcontainers Valkey 并返回连接串。"""
    with RedisContainer("valkey/valkey:9") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


async def test_pubsub_roundtrip(valkey_url: str) -> None:
    """发布后订阅方能收到通知（channel 按 run_id 隔离）。"""
    notifier = ValkeyEventNotifier(valkey_url)
    received: list[str] = []

    async def consume(run_id: str) -> None:
        async for _ in notifier.subscribe(run_id):
            received.append(run_id)
            return

    task_a = asyncio.create_task(consume("run-a"))
    task_b = asyncio.create_task(consume("run-b"))
    await asyncio.sleep(0.3)  # 等待订阅在 Valkey 侧建立

    await notifier.notify("run-a")
    await notifier.notify("run-b")

    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=5)
    await notifier.aclose()

    assert sorted(received) == ["run-a", "run-b"]
