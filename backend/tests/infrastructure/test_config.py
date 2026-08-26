"""应用环境配置测试。"""

import pytest

from literature_agent.infrastructure.config import Settings


def test_chat_json_schema_supported_defaults_to_true(monkeypatch) -> None:
    """默认使用严格 JSON Schema，保持既有 Provider 契约。"""
    monkeypatch.delenv("AGENT_CHAT_JSON_SCHEMA_SUPPORTED", raising=False)

    assert Settings.from_env().chat_json_schema_supported is True


def test_chat_json_schema_supported_can_fallback_to_json_object(monkeypatch) -> None:
    """不支持 JSON Schema 的 Provider 可显式选择 JSON Object。"""
    monkeypatch.setenv("AGENT_CHAT_JSON_SCHEMA_SUPPORTED", "false")

    assert Settings.from_env().chat_json_schema_supported is False


def test_arxiv_backend_defaults_offline_and_requires_explicit_real(monkeypatch) -> None:
    """未设置开关时必须 fail closed 到 Fake，真实 HTTP 需显式选择。"""
    monkeypatch.delenv("AGENT_ARXIV_BACKEND", raising=False)
    assert Settings.from_env().arxiv_backend == "fake"

    monkeypatch.setenv("AGENT_ARXIV_BACKEND", "httpx")
    assert Settings.from_env().arxiv_backend == "httpx"


def test_research_agent_runtime_defaults_offline(monkeypatch) -> None:
    """未显式启用时 Research Agent 必须保持 Fake 且不读取 Provider Secret。"""
    monkeypatch.delenv("AGENT_RESEARCH_RUNTIME_BACKEND", raising=False)
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_API_KEY", "not-read-in-fake-mode")

    settings = Settings.from_env()

    assert settings.research_runtime_backend == "fake"
    assert settings.research_model_api_key is None


def test_research_agent_real_settings_are_separate_and_secret_is_hidden(monkeypatch) -> None:
    """真实 Agent Provider 配置不得复用 RAG/Review Chat 配置或泄漏 Key。"""
    monkeypatch.setenv("AGENT_RESEARCH_RUNTIME_BACKEND", "deep_agents")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_BASE_URL", "https://agent.example/v1")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_API_KEY", "agent-secret-value")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS", "1536")
    monkeypatch.setenv("AGENT_CHAT_API_KEY", "review-secret-value")

    settings = Settings.from_env()

    assert settings.research_runtime_backend == "deep_agents"
    assert settings.research_model_base_url == "https://agent.example/v1"
    assert settings.research_model_api_key == "agent-secret-value"
    assert settings.research_model == "deepseek-v4-flash"
    assert settings.research_model_max_output_tokens == 1536
    assert "agent-secret-value" not in repr(settings)
    assert "review-secret-value" not in repr(settings)


@pytest.mark.parametrize("backend", ["unknown", "DEEP_AGENTS"])
def test_research_agent_runtime_rejects_unknown_backend(monkeypatch, backend: str) -> None:
    """拼写错误或大小写漂移不能静默回退。"""
    monkeypatch.setenv("AGENT_RESEARCH_RUNTIME_BACKEND", backend)

    with pytest.raises(ValueError, match="AGENT_RESEARCH_RUNTIME_BACKEND"):
        Settings.from_env()


def test_research_agent_real_mode_does_not_borrow_chat_key(monkeypatch) -> None:
    """共享 Settings 不借用 Chat Key；缺 Key 由持有 Secret 的 Worker fail-fast。"""
    monkeypatch.setenv("AGENT_RESEARCH_RUNTIME_BACKEND", "deep_agents")
    monkeypatch.delenv("AGENT_RESEARCH_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_CHAT_API_KEY", "must-not-be-reused")

    settings = Settings.from_env()

    assert settings.research_runtime_backend == "deep_agents"
    assert settings.research_model_api_key is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENT_RESEARCH_MODEL", "deepseek-chat"),
        ("AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS", "0"),
        ("AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS", "invalid"),
    ],
)
def test_research_agent_real_mode_rejects_unbounded_or_drifted_model_settings(
    monkeypatch, name: str, value: str
) -> None:
    """真实模式必须固定模型并拒绝无效输出上限。"""
    monkeypatch.setenv("AGENT_RESEARCH_RUNTIME_BACKEND", "deep_agents")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_API_KEY", "agent-secret")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        Settings.from_env()


def test_worker_metrics_port_defaults_and_can_be_disabled(monkeypatch) -> None:
    """Worker Metrics 默认使用本地 8001，显式 0 时关闭。"""
    monkeypatch.delenv("AGENT_WORKER_METRICS_PORT", raising=False)
    assert Settings.from_env().worker_metrics_port == 8001

    monkeypatch.setenv("AGENT_WORKER_METRICS_PORT", "0")
    assert Settings.from_env().worker_metrics_port == 0


def test_worker_metrics_port_rejects_invalid_range(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKER_METRICS_PORT", "65536")

    with pytest.raises(ValueError, match="AGENT_WORKER_METRICS_PORT"):
        Settings.from_env()


def test_worker_metrics_port_rejects_non_integer_with_setting_name(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKER_METRICS_PORT", "not-a-port")

    with pytest.raises(
        ValueError, match="AGENT_WORKER_METRICS_PORT 必须为 0..65535 的整数"
    ):
        Settings.from_env()
