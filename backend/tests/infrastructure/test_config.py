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
