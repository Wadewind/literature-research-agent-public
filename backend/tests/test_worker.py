"""Worker 入口配置测试。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from literature_agent.application.project_research_context_service import (
    ProjectResearchContextService,
)
from literature_agent.application.run_execution_service import ExecutionOutcome
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlService,
)
from literature_agent.domain.tokenization import OFFLINE_TOKENIZER
from literature_agent.infrastructure.agent.fake_research_agent_runtime import (
    FakeResearchAgentRuntime,
)
from literature_agent.infrastructure.config import Settings
from literature_agent.metrics import metrics
from literature_agent.observability import get_log_context
from literature_agent.worker import (
    _build_arxiv_gateway,
    _build_model_stack,
    _build_research_agent_runtime,
    _dependency_reconcile_loop,
    _open_research_agent_runtime,
    _shutdown,
    execute_run,
    make_worker_settings,
)


def test_make_worker_settings_registers_execute_run() -> None:
    """WorkerSettings 应注册 execute_run 并禁用 ARQ 自动重试。"""
    settings = Settings(redis_url="redis://example.internal:6380/2")

    worker_settings = make_worker_settings(settings)

    [registered] = worker_settings.functions
    assert registered.coroutine is execute_run
    assert registered.keep_result_s == 0
    assert worker_settings.keep_result == 0
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


def test_fake_research_runtime_does_not_construct_provider(monkeypatch) -> None:
    """默认 Fake 不构造模型、不打开 Checkpointer，也不触网。"""
    monkeypatch.setattr(
        "literature_agent.worker.build_deepseek_research_model",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得构造 Provider")),
    )

    runtime = _build_research_agent_runtime(
        Settings(research_runtime_backend="fake"),
        session_factory=lambda: None,  # type: ignore[arg-type]
        retriever=object(),  # type: ignore[arg-type]
        event_notifier=object(),  # type: ignore[arg-type]
        checkpoint_factory=None,
        workspace_manager=None,
        runtime_owner_id="worker-1",
    )

    assert isinstance(runtime, FakeResearchAgentRuntime)


async def test_open_fake_research_runtime_does_not_open_provider_resources(
    monkeypatch,
) -> None:
    """Fake 生命周期不得构造 Provider 或连接 Agent Checkpointer。"""
    monkeypatch.setattr(
        "literature_agent.worker.build_deepseek_research_model",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得构造 Provider")),
    )

    class ForbiddenCheckpointPool:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("不得创建 Agent Checkpointer")

    monkeypatch.setattr(
        "literature_agent.worker.PostgresCheckpointPool", ForbiddenCheckpointPool
    )

    async with _open_research_agent_runtime(
        Settings(research_runtime_backend="fake"),
        session_factory=lambda: None,  # type: ignore[arg-type]
        retriever=object(),  # type: ignore[arg-type]
        event_notifier=object(),  # type: ignore[arg-type]
        runtime_owner_id="worker-1",
    ) as runtime:
        assert isinstance(runtime, FakeResearchAgentRuntime)


def test_deep_research_runtime_uses_production_context_and_control(monkeypatch) -> None:
    """Deep 模式必须装配持久恢复控制、Project context 与真实 Adapter。"""
    captured: dict[str, object] = {}
    model = object()
    checkpointer = object()
    checkpoint_factory = object()
    workspace_manager = object()

    def build_model(**kwargs: object) -> object:
        captured["model_kwargs"] = kwargs
        return model

    monkeypatch.setattr(
        "literature_agent.worker.build_deepseek_research_model", build_model
    )

    class StubRuntime:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "literature_agent.worker.DeepAgentsResearchAgentRuntime", StubRuntime
    )

    class StubSandboxedRuntime:
        def __init__(self, **kwargs: object) -> None:
            captured["wrapper_kwargs"] = kwargs

    monkeypatch.setattr(
        "literature_agent.worker.SandboxedResearchAgentRuntime", StubSandboxedRuntime
    )
    settings = Settings(
        research_runtime_backend="deep_agents",
        research_model_api_key="agent-secret",
    )

    runtime = _build_research_agent_runtime(
        settings,
        session_factory=lambda: None,  # type: ignore[arg-type]
        retriever=object(),  # type: ignore[arg-type]
        event_notifier=object(),  # type: ignore[arg-type]
        checkpoint_factory=checkpoint_factory,  # type: ignore[arg-type]
        workspace_manager=workspace_manager,  # type: ignore[arg-type]
        runtime_owner_id="worker-1",
    )

    assert isinstance(runtime, StubSandboxedRuntime)
    wrapper_kwargs = captured["wrapper_kwargs"]
    assert isinstance(wrapper_kwargs, dict)
    assert wrapper_kwargs["checkpoint_factory"] is checkpoint_factory
    assert wrapper_kwargs["workspace_manager"] is workspace_manager
    async def before_succeed(request: object) -> None:
        del request

    wrapper_kwargs["runtime_factory"](checkpointer, object(), before_succeed)
    assert captured["checkpointer"] is checkpointer
    assert captured["model"] is model
    assert isinstance(captured["execution_control"], RuntimeExecutionControlService)
    assert isinstance(captured["project_context"], ProjectResearchContextService)
    assert captured["runtime_owner_id"] == "worker-1"
    assert captured["before_succeed"] is before_succeed
    assert captured["model_kwargs"] == {
        "base_url": "https://api.deepseek.com",
        "api_key": "agent-secret",
        "model": "deepseek-v4-flash",
        "max_output_tokens": 2048,
        "timeout_seconds": 60.0,
        "max_retries": 2,
    }


def test_unknown_research_runtime_fails_closed() -> None:
    """直接构造 Settings 也不能使未知 Runtime 静默回退。"""
    with pytest.raises(ValueError, match="research_runtime_backend"):
        _build_research_agent_runtime(
            Settings(research_runtime_backend="unknown"),
            session_factory=lambda: None,  # type: ignore[arg-type]
            retriever=object(),  # type: ignore[arg-type]
            event_notifier=object(),  # type: ignore[arg-type]
            checkpoint_factory=None,
            workspace_manager=None,
            runtime_owner_id="worker-1",
        )


async def test_open_deep_runtime_requires_key_before_opening_resources(
    monkeypatch,
) -> None:
    """真实 Worker 缺专用 Key 时必须在模型或 Checkpointer I/O 前 fail-fast。"""
    monkeypatch.setattr(
        "literature_agent.worker.build_deepseek_research_model",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得构造 Provider")),
    )

    class ForbiddenCheckpointPool:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("不得创建 Agent Checkpointer")

    monkeypatch.setattr(
        "literature_agent.worker.PostgresCheckpointPool", ForbiddenCheckpointPool
    )

    with pytest.raises(ValueError, match="AGENT_RESEARCH_MODEL_API_KEY") as exc_info:
        async with _open_research_agent_runtime(
            Settings(
                research_runtime_backend="deep_agents",
                research_model_api_key=None,
            ),
            session_factory=lambda: None,  # type: ignore[arg-type]
            retriever=object(),  # type: ignore[arg-type]
            event_notifier=object(),  # type: ignore[arg-type]
            runtime_owner_id="worker-1",
        ):
            raise AssertionError("缺 Key 不得进入 Runtime 生命周期")

    assert "must-not-leak" not in str(exc_info.value)


async def test_open_deep_runtime_owns_checkpoint_and_model_lifecycle(monkeypatch) -> None:
    """Worker 资源上下文关闭时必须释放模型与持久 Checkpointer。"""
    events: list[str] = []
    checkpointer = object()
    runtime = object()

    class StubStore:
        def __init__(self, database_url: str, **kwargs: object) -> None:
            assert kwargs == {"min_size": 1, "max_size": 4}
            assert database_url == "postgresql+psycopg://agent:agent@db/agent"

        @asynccontextmanager
        async def open(self):
            events.append("checkpoint_open")
            try:
                yield checkpointer
            finally:
                events.append("checkpoint_close")

    model = object()
    monkeypatch.setattr("literature_agent.worker.PostgresCheckpointPool", StubStore)
    monkeypatch.setattr(
        "literature_agent.worker.build_deepseek_research_model", lambda **_: model
    )
    close_model = AsyncMock(side_effect=lambda _: events.append("model_close"))
    monkeypatch.setattr("literature_agent.worker.aclose_deepseek_research_model", close_model)
    monkeypatch.setattr(
        "literature_agent.worker._build_research_agent_runtime", lambda *_, **__: runtime
    )

    async with _open_research_agent_runtime(
        Settings(
            database_url="postgresql+psycopg://agent:agent@db/agent",
            research_runtime_backend="deep_agents",
            research_model_api_key="secret",
        ),
        session_factory=lambda: None,  # type: ignore[arg-type]
        retriever=object(),  # type: ignore[arg-type]
        event_notifier=object(),  # type: ignore[arg-type]
        runtime_owner_id="worker-1",
    ) as opened:
        assert opened is runtime
        assert events == ["checkpoint_open"]

    close_model.assert_awaited_once_with(model)
    assert events == ["checkpoint_open", "checkpoint_close", "model_close"]


async def test_shutdown_closes_research_runtime_resources() -> None:
    """Worker shutdown 必须释放长期 Checkpointer/Provider 资源。"""
    resources = AsyncMock()

    await _shutdown({"agent_runtime_resources": resources})

    resources.aclose.assert_awaited_once_with()


async def test_execute_run_builds_bounded_worker_context_without_job_payload() -> None:
    """Worker 从本地 Job 事实构造有界关联标识，退出后不泄漏上下文。"""
    service = AsyncMock()
    observed: dict = {}

    async def execute(run_id: str, correlation_id: str):
        observed.update(get_log_context())
        observed["active_jobs"] = metrics.registry.get_sample_value(
            "agent_worker_active_jobs"
        )
        observed["argument"] = correlation_id
        assert run_id == "run-1"
        return ExecutionOutcome.COMPLETED

    service.execute.side_effect = execute
    long_job_id = "job-secret-payload-" + "x" * 500

    result = await execute_run(
        {"run_execution_service": service, "job_id": long_job_id}, "run-1"
    )

    assert result == "completed"
    assert observed["service"] == "worker"
    assert observed["run_id"] == "run-1"
    assert observed["correlation_id"] == observed["argument"]
    assert observed["correlation_id"].startswith("worker:")
    assert len(observed["correlation_id"]) == 31
    assert long_job_id not in observed["correlation_id"]
    assert observed["active_jobs"] == 1
    assert metrics.registry.get_sample_value("agent_worker_active_jobs") == 0
    assert get_log_context() == {}
