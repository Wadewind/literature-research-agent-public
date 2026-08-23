"""HTTP Correlation ID 中间件测试。"""

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_correlation_id
from literature_agent.observability import CorrelationMiddleware, get_log_context


def test_valid_header_is_echoed_and_available_to_dependency() -> None:
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware, service="api")

    @app.get("/probe")
    async def probe(
        correlation_id: Annotated[str, Depends(get_correlation_id)],
    ) -> dict[str, str]:
        return {"correlation_id": correlation_id}

    with TestClient(app) as client:
        response = client.get("/probe", headers={"X-Correlation-ID": "client:abc-1"})

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "client:abc-1"
    assert response.json() == {"correlation_id": "client:abc-1"}
    assert get_log_context() == {}


def test_missing_or_invalid_header_gets_safe_uuid_and_context_resets() -> None:
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware, service="api")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    for supplied in (None, "bad id with spaces", "x" * 129):
        headers = {} if supplied is None else {"X-Correlation-ID": supplied}
        with TestClient(app) as client:
            response = client.get("/health/live", headers=headers)
        value = response.headers["X-Correlation-ID"]
        assert value != supplied
        assert len(value) == 36
        assert get_log_context() == {}


def test_handled_error_echoes_header_without_logging_query_or_headers(caplog) -> None:
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware, service="api")

    @app.get("/failure")
    async def failure(request: Request) -> None:
        del request
        raise ValueError("private-body")

    # Starlette 的 ServerErrorMiddleware 负责把异常变成 500；关闭再次抛出。
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/failure?secret=query", headers={"X-Correlation-ID": "failure-1"})
    assert response.status_code == 500
    assert response.headers["X-Correlation-ID"] == "failure-1"
    record = next(r for r in caplog.records if getattr(r, "event", None) == "request_failed")
    assert record.path == "/failure"
    assert not hasattr(record, "query")
    assert not hasattr(record, "headers")
    assert "private-body" not in record.getMessage()
    assert record.exc_info is None
    assert get_log_context() == {}


def test_unmatched_route_never_logs_raw_path_segments(caplog) -> None:
    """404 未匹配路由不得把可能敏感的原始 path 写入日志。"""
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware, service="api")
    secret_path = "/missing/private-paper-title-secret"

    with TestClient(app) as client:
        response = client.get(secret_path, headers={"X-Correlation-ID": "not-found-1"})

    assert response.status_code == 404
    record = next(
        r
        for r in caplog.records
        if getattr(r, "event", None) == "request_completed"
        and getattr(r, "correlation_id", None) is None
    )
    assert record.path == "[unmatched]"
    assert "private-paper-title-secret" not in record.getMessage()
