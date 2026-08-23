"""Worker 入口配置测试。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from literature_agent.domain.tokenization import OFFLINE_TOKENIZER
from literature_agent.infrastructure.config import Settings
from literature_agent.worker import (
    _build_arxiv_gateway,
    _build_model_stack,
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


def test_fake_mode_builds_offline_arxiv_gateway() -> None:
    """生产组装边界必须显式选择不持有网络客户端的 Fake arXiv。"""
    gateway, closables = _build_arxiv_gateway(Settings(arxiv_backend="fake"))

    assert gateway.__class__.__name__ == "FixtureArxivGateway"
    assert closables == []


def test_real_mode_builds_http_arxiv_gateway() -> None:
    """只有显式 real 配置才装配真实 HTTP arXiv Adapter。"""
    gateway, closables = _build_arxiv_gateway(Settings(arxiv_backend="httpx"))

    assert gateway.__class__.__name__ == "HttpxArxivGateway"
    assert closables == [gateway]


def test_unknown_arxiv_backend_fails_closed() -> None:
    """遗漏或拼错 Adapter 不能静默回退到真实网络。"""
    with pytest.raises(ValueError, match="arxiv_backend"):
        _build_arxiv_gateway(Settings(arxiv_backend="unknown"))


def test_fake_model_stack_uses_offline_tokenizer() -> None:
    """Fake Indexing/RAG 必须共享不读取外部词表的确定性 tokenizer。"""
    _gateway, profile, closables = _build_model_stack(Settings(), lambda: None)  # type: ignore[arg-type]

    assert profile.tokenizer == OFFLINE_TOKENIZER
    assert closables == []
