"""Worker 入口配置测试。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from literature_agent.infrastructure.config import Settings
from literature_agent.worker import (
    _dependency_reconcile_loop,
    execute_run,
    make_worker_settings,
)


def test_make_worker_settings_registers_execute_run() -> None:
    """WorkerSettings 应注册 execute_run 并禁用 ARQ 自动重试。"""
    settings = Settings(redis_url="redis://example.internal:6380/2")

    worker_settings = make_worker_settings(settings)

    assert execute_run in worker_settings.functions
    assert worker_settings.max_tries == 1
    assert worker_settings.on_startup is not None
    assert worker_settings.on_shutdown is not None
    assert worker_settings.redis_settings.host == "example.internal"
    assert worker_settings.redis_settings.port == 6380
    assert worker_settings.redis_settings.database == 2


async def test_dependency_reconcile_loop_is_independent(monkeypatch) -> None:
    """依赖对账使用独立服务循环，取消时不会吞掉 CancelledError。"""
    service = AsyncMock()
    service.reconcile_waiting.return_value = 1
    sleep = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    ctx = {
        "review_dependency_reconciler": service,
        "settings": Settings(worker_reconcile_interval_seconds=0.01),
    }

    with pytest.raises(asyncio.CancelledError):
        await _dependency_reconcile_loop(ctx)

    service.reconcile_waiting.assert_awaited_once_with()
