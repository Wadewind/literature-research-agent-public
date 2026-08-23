"""结构化日志与上下文传播测试。"""

import asyncio
import json
import logging
from io import StringIO

import pytest

from literature_agent.observability import (
    JsonLogFormatter,
    bind_log_context,
    configure_logging,
    get_log_context,
    log_event,
)


def _render(*, extras: dict | None = None, exc_info=None) -> dict:
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "敏感正文不应成为 event", (), exc_info
    )
    for key, value in (extras or {}).items():
        setattr(record, key, value)
    return json.loads(JsonLogFormatter().format(record))


def test_json_formatter_uses_utc_and_strict_allowlist() -> None:
    with bind_log_context(service="api", correlation_id="corr-1", project_id="p-1"):
        payload = _render(
            extras={
                "event": "request_completed",
                "status_code": 200,
                "duration_ms": 4,
                "authorization": "Bearer secret",
                "prompt": "private question",
                "random_extra": "must-not-leak",
            }
        )

    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "INFO"
    assert payload["event"] == "request_completed"
    assert payload["service"] == "api"
    assert payload["correlation_id"] == "corr-1"
    assert payload["project_id"] == "p-1"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 4
    assert "authorization" not in payload
    assert "prompt" not in payload
    assert "random_extra" not in payload
    assert "敏感正文" not in json.dumps(payload, ensure_ascii=False)


def test_exception_is_reduced_to_safe_type_without_message_or_traceback() -> None:
    secret = "sk-live-secret PDF正文"
    try:
        raise RuntimeError(secret)
    except RuntimeError:
        import sys

        payload = _render(extras={"event": "request_failed"}, exc_info=sys.exc_info())

    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["exception_type"] == "RuntimeError"
    assert secret not in rendered
    assert "Traceback" not in rendered


@pytest.mark.asyncio
async def test_context_is_nested_async_isolated_and_restored() -> None:
    assert get_log_context() == {}
    with bind_log_context(service="worker", correlation_id="outer"):
        assert get_log_context()["correlation_id"] == "outer"
        with bind_log_context(run_id="run-1", correlation_id="inner"):
            assert get_log_context()["correlation_id"] == "inner"
        assert get_log_context()["correlation_id"] == "outer"

        async def task(value: str) -> str:
            with bind_log_context(correlation_id=value):
                await asyncio.sleep(0)
                return get_log_context()["correlation_id"]

        assert await asyncio.gather(task("a"), task("b")) == ["a", "b"]
        assert get_log_context()["correlation_id"] == "outer"
    assert get_log_context() == {}


def test_repeated_configuration_does_not_stack_project_handlers() -> None:
    root = logging.getLogger()
    access_logger = logging.getLogger("uvicorn.access")
    before = list(root.handlers)
    before_access = list(access_logger.handlers)
    access_handler = logging.StreamHandler()
    access_logger.handlers = [access_handler]
    try:
        configure_logging(service="api")
        configure_logging(service="api")
        owned = [h for h in root.handlers if getattr(h, "_literature_agent_json", False)]
        assert len(owned) == 1
        assert isinstance(owned[0].formatter, JsonLogFormatter)
        assert isinstance(access_handler.formatter, JsonLogFormatter)
    finally:
        root.handlers[:] = before
        access_logger.handlers[:] = before_access


def test_log_event_never_serializes_unapproved_values() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("structured-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    with bind_log_context(service="worker", correlation_id="corr"):
        log_event(
            logger,
            logging.INFO,
            "model_request_completed",
            provider="fake",
            model="fake-chat",
            messages=[{"content": "secret"}],
        )
    payload = json.loads(stream.getvalue())
    assert payload["event"] == "model_request_completed"
    assert payload["provider"] == "fake"
    assert "messages" not in payload


def test_formatter_never_calls_custom_string_conversion_for_allowed_fields() -> None:
    """恶意对象即使进入允许字段也只能得到固定占位符。"""

    class _SecretObject:
        def __str__(self) -> str:
            raise AssertionError("secret-from-custom-str")

    value = _SecretObject()
    with bind_log_context(service=value, correlation_id=value):
        payload = _render(
            extras={
                "event": value,
                "provider": value,
                "duration_ms": value,
            }
        )

    assert payload["event"] == "[invalid]"
    assert payload["service"] == "[invalid]"
    assert payload["correlation_id"] == "[invalid]"
    assert payload["provider"] == "[invalid]"
    assert payload["duration_ms"] == "[invalid]"
    assert "secret-from-custom-str" not in json.dumps(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_formatter_rejects_non_finite_floats(value: float) -> None:
    payload = _render(extras={"event": "safe_event", "duration_ms": value})

    rendered = json.dumps(payload)
    assert payload["duration_ms"] == "[invalid]"
    assert "NaN" not in rendered
    assert "Infinity" not in rendered
