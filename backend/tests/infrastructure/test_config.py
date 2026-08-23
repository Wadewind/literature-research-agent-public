"""应用环境配置测试。"""

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
