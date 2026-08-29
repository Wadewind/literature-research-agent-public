"""应用环境配置测试。"""

import logging

import pytest

from literature_agent.infrastructure.config import Settings


def test_job_timeout_defaults_to_independent_run_budget(monkeypatch) -> None:
    """完整 Job 默认有独立预算，不能再由单次 Parser 超时推导。"""
    monkeypatch.delenv("AGENT_PARSER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_JOB_TIMEOUT_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.parser_timeout_seconds == 300
    assert settings.job_timeout_seconds == 1800


def test_job_timeout_accepts_explicit_budget_larger_than_parser(monkeypatch) -> None:
    """部署者可分别调整完整 Job 与单次 Parser 预算。"""
    monkeypatch.setenv("AGENT_PARSER_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("AGENT_JOB_TIMEOUT_SECONDS", "2400")

    settings = Settings.from_env()

    assert settings.parser_timeout_seconds == 600
    assert settings.job_timeout_seconds == 2400


@pytest.mark.parametrize(
    ("parser_timeout", "job_timeout"),
    [("300", "300"), ("300", "299"), ("300", "invalid")],
)
def test_job_timeout_rejects_invalid_or_insufficient_budget(
    monkeypatch, parser_timeout: str, job_timeout: str
) -> None:
    """Job 必须给 Parser 之外的完整 Run 编排留下时间。"""
    monkeypatch.setenv("AGENT_PARSER_TIMEOUT_SECONDS", parser_timeout)
    monkeypatch.setenv("AGENT_JOB_TIMEOUT_SECONDS", job_timeout)

    with pytest.raises(ValueError, match="AGENT_JOB_TIMEOUT_SECONDS"):
        Settings.from_env()


def test_log_level_defaults_to_info_and_accepts_case_insensitive_name(monkeypatch) -> None:
    """API 与 Worker 默认保留 INFO，显式等级名允许常见的小写写法。"""
    monkeypatch.delenv("AGENT_LOG_LEVEL", raising=False)
    assert Settings.from_env().log_level == logging.INFO

    monkeypatch.setenv("AGENT_LOG_LEVEL", " warning ")
    assert Settings.from_env().log_level == logging.WARNING


def test_log_level_rejects_unknown_name(monkeypatch) -> None:
    """拼写错误不能静默回退到默认等级。"""
    monkeypatch.setenv("AGENT_LOG_LEVEL", "verbose")

    with pytest.raises(
        ValueError,
        match="AGENT_LOG_LEVEL 必须为 DEBUG、INFO、WARNING、ERROR 或 CRITICAL",
    ):
        Settings.from_env()


def test_chat_json_schema_supported_defaults_to_true(monkeypatch) -> None:
    """默认使用严格 JSON Schema，保持既有 Provider 契约。"""
    monkeypatch.delenv("AGENT_CHAT_JSON_SCHEMA_SUPPORTED", raising=False)

    assert Settings.from_env().chat_json_schema_supported is True


def test_chat_json_schema_supported_can_fallback_to_json_object(monkeypatch) -> None:
    """不支持 JSON Schema 的 Provider 可显式选择 JSON Object。"""
    monkeypatch.setenv("AGENT_CHAT_JSON_SCHEMA_SUPPORTED", "false")

    assert Settings.from_env().chat_json_schema_supported is False


def test_model_thinking_defaults_disabled_with_low_debug_effort(monkeypatch) -> None:
    """默认关闭 thinking；低 effort 只作为显式调试开启时的安全起点。"""
    for name in (
        "AGENT_CHAT_THINKING_MODE",
        "AGENT_CHAT_REASONING_EFFORT",
        "AGENT_RESEARCH_MODEL_THINKING_MODE",
        "AGENT_RESEARCH_MODEL_REASONING_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.chat_thinking_mode == "disabled"
    assert settings.chat_reasoning_effort == "low"
    assert settings.research_model_thinking_mode == "disabled"
    assert settings.research_model_reasoning_effort == "low"
    assert settings.answer_max_output_tokens == 4_096
    assert settings.research_model_max_output_tokens == 4_096


def test_model_thinking_can_be_enabled_only_in_debug_mode(monkeypatch) -> None:
    """thinking 是开发诊断开关，不得在非 debug 进程意外启用。"""
    monkeypatch.setenv("AGENT_DEBUG", "true")
    monkeypatch.setenv("AGENT_CHAT_THINKING_MODE", "enabled")
    monkeypatch.setenv("AGENT_CHAT_REASONING_EFFORT", "high")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_THINKING_MODE", "enabled")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_REASONING_EFFORT", "max")

    settings = Settings.from_env()

    assert settings.chat_thinking_mode == "enabled"
    assert settings.chat_reasoning_effort == "high"
    assert settings.research_model_thinking_mode == "enabled"
    assert settings.research_model_reasoning_effort == "max"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENT_CHAT_THINKING_MODE", "auto"),
        ("AGENT_CHAT_REASONING_EFFORT", "medium"),
        ("AGENT_RESEARCH_MODEL_THINKING_MODE", "auto"),
        ("AGENT_RESEARCH_MODEL_REASONING_EFFORT", "xhigh"),
    ],
)
def test_model_thinking_rejects_unregistered_values(
    monkeypatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(
    "name", ["AGENT_CHAT_THINKING_MODE", "AGENT_RESEARCH_MODEL_THINKING_MODE"]
)
def test_model_thinking_rejects_enabled_outside_debug(monkeypatch, name: str) -> None:
    monkeypatch.delenv("AGENT_DEBUG", raising=False)
    monkeypatch.setenv(name, "enabled")

    with pytest.raises(ValueError, match="AGENT_DEBUG"):
        Settings.from_env()


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


def test_research_agent_sandbox_image_defaults_to_slice_7_3(monkeypatch) -> None:
    """真实模式默认使用包含固定 MCP recipe 的当前派生镜像。"""
    monkeypatch.setenv("AGENT_RESEARCH_RUNTIME_BACKEND", "deep_agents")
    monkeypatch.delenv("AGENT_RESEARCH_SANDBOX_IMAGE", raising=False)

    assert (
        Settings.from_env().research_sandbox_image
        == "agent-service/research-agent-sandbox@sha256:"
        "8ded4a3cfb5603efac3e297a09f79f4bdef798379728eeb96d563ae8f99f40d1"
    )


def test_research_agent_real_settings_are_separate_and_secret_is_hidden(monkeypatch) -> None:
    """真实 Agent Provider 配置不得复用 RAG/Review Chat 配置或泄漏 Key。"""
    monkeypatch.setenv("AGENT_RESEARCH_RUNTIME_BACKEND", "deep_agents")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_BASE_URL", "https://agent.example/v1")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_API_KEY", "agent-secret-value")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS", "3072")
    monkeypatch.setenv("AGENT_RESEARCH_SANDBOX_DOMAIN", "sandbox.example:443")
    monkeypatch.setenv("AGENT_RESEARCH_SANDBOX_PROTOCOL", "https")
    monkeypatch.setenv("AGENT_RESEARCH_SANDBOX_API_KEY", "sandbox-secret-value")
    monkeypatch.setenv("AGENT_RESEARCH_SANDBOX_IMAGE", "research-sandbox:fixed")
    monkeypatch.setenv("AGENT_CHAT_API_KEY", "review-secret-value")

    settings = Settings.from_env()

    assert settings.research_runtime_backend == "deep_agents"
    assert settings.research_model_base_url == "https://agent.example/v1"
    assert settings.research_model_api_key == "agent-secret-value"
    assert settings.research_model == "deepseek-v4-flash"
    assert settings.research_model_max_output_tokens == 3072
    assert settings.research_sandbox_domain == "sandbox.example:443"
    assert settings.research_sandbox_protocol == "https"
    assert settings.research_sandbox_api_key == "sandbox-secret-value"
    assert settings.research_sandbox_image == "research-sandbox:fixed"
    assert "agent-secret-value" not in repr(settings)
    assert "sandbox-secret-value" not in repr(settings)
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
        ("AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS", "4097"),
        ("AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS", "invalid"),
        ("AGENT_RESEARCH_SANDBOX_PROTOCOL", "ftp"),
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
