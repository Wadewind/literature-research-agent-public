"""标准库 JSON 日志、Correlation ID 与进程内上下文。

本模块只依赖 Python 标准库和 ASGI 协议，可被 API、Application 与 Worker
共同使用，而不会让 Domain 依赖 FastAPI 或 ARQ。日志采用显式字段白名单；
未批准的 ``extra``、消息正文与异常 traceback 一律不会序列化。
"""

from __future__ import annotations

import contextvars
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = b"x-correlation-id"
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_CONTEXT_FIELDS = frozenset(
    {
        "service",
        "correlation_id",
        "run_id",
        "project_id",
        "attempt_id",
        "run_type",
        "stage",
    }
)
_EVENT_FIELDS = frozenset(
    {
        *_CONTEXT_FIELDS,
        "duration_ms",
        "error_code",
        "exception_type",
        "status",
        "operation",
        "provider",
        "model",
        "method",
        "path",
        "status_code",
        "outbox_id",
        "count",
        "semantic_count",
        "fulltext_count",
        "merged_count",
        "evidence_count",
    }
)

_log_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "literature_agent_log_context", default=None
)


def is_valid_correlation_id(value: str | None) -> bool:
    """判断客户端 Correlation ID 是否满足有界安全字符契约。"""
    return bool(value and _CORRELATION_PATTERN.fullmatch(value))


def new_correlation_id() -> str:
    """生成不含业务或用户输入的随机 Correlation ID。"""
    return str(uuid.uuid4())


def get_log_context() -> dict[str, Any]:
    """返回当前任务的日志上下文副本。"""
    return dict(_log_context.get() or {})


def current_correlation_id() -> str:
    """返回当前关联标识；无绑定时生成一个安全标识。"""
    value = (_log_context.get() or {}).get("correlation_id")
    return str(value) if value else new_correlation_id()


@contextmanager
def bind_log_context(**fields: Any) -> Iterator[None]:
    """在当前同步/异步任务作用域绑定白名单上下文，退出后精确恢复。"""
    updated = get_log_context()
    updated.update({key: value for key, value in fields.items() if key in _CONTEXT_FIELDS})
    token = _log_context.set(updated)
    try:
        yield
    finally:
        _log_context.reset(token)


class JsonLogFormatter(logging.Formatter):
    """输出单行 JSON，并拒绝隐式消息、任意 extra 与异常正文。"""

    def __init__(self, *, default_service: str = "unknown") -> None:
        super().__init__()
        self._default_service = default_service

    def format(self, record: logging.LogRecord) -> str:
        context = get_log_context()
        event = getattr(record, "event", None)
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "event": _json_scalar(event) if event is not None else "unstructured_log",
            "service": _json_scalar(context.get("service", self._default_service)),
            "correlation_id": _json_scalar(context.get("correlation_id", "-")),
        }
        for field in _EVENT_FIELDS:
            value = getattr(record, field, context.get(field))
            if value is not None and field not in payload:
                payload[field] = _json_scalar(value)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = _json_scalar(record.exc_info[0].__name__)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )


def _json_scalar(value: Any) -> str | int | float | bool | None:
    """只接受 JSON 安全标量；绝不调用任意对象的 ``str``。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return "[invalid]"


class _ProjectJsonHandler(logging.StreamHandler):
    """用于识别项目自有 Handler，避免重复配置叠加。"""

    _literature_agent_json = True


def configure_logging(*, service: str, level: int = logging.INFO) -> None:
    """幂等配置项目 JSON Handler，不删除宿主或测试框架已有 Handler。"""
    root = logging.getLogger()
    formatter = JsonLogFormatter(default_service=service)
    owned = [handler for handler in root.handlers if isinstance(handler, _ProjectJsonHandler)]
    if owned:
        handler = owned[0]
    else:
        handler = _ProjectJsonHandler()
        root.addHandler(handler)
    handler.setFormatter(formatter)
    handler.setLevel(level)
    root.setLevel(level)
    # Uvicorn 默认使用不向 root 传播的自有 Handler；应用工厂在其配置完成后
    # 被调用，因此在这里统一 Formatter，避免 access/error 行退回纯文本。
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for external_handler in logging.getLogger(logger_name).handlers:
            external_handler.setFormatter(formatter)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    exc: BaseException | None = None,
    **fields: Any,
) -> None:
    """记录固定事件；只有字段白名单会进入 LogRecord。"""
    extra = {"event": event}
    extra.update({key: value for key, value in fields.items() if key in _EVENT_FIELDS})
    if exc is not None:
        extra["exception_type"] = type(exc).__name__
    try:
        logger.log(level, event, extra=extra)
    except Exception:
        # 可观测性永远不能改变业务结果；这里也不能再递归记录日志。
        return


class CorrelationMiddleware:
    """接受/生成 Correlation ID、绑定请求上下文并回显响应头。"""

    def __init__(self, app: ASGIApp, *, service: str = "api") -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = _header_value(scope, CORRELATION_HEADER)
        correlation_id = (
            supplied
            if supplied is not None and is_valid_correlation_id(supplied)
            else new_correlation_id()
        )
        started = time.monotonic()
        status_code = 500
        response_started = False

        async def send_with_header(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers = [
                    (key, value) for key, value in headers if key.lower() != CORRELATION_HEADER
                ]
                headers.append((CORRELATION_HEADER, correlation_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with bind_log_context(service=self.service, correlation_id=correlation_id):
            try:
                await self.app(scope, receive, send_with_header)
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                log_event(
                    logging.getLogger("literature_agent.api"),
                    logging.ERROR,
                    "request_failed",
                    exc=exc,
                    method=scope.get("method"),
                    path=_route_path(scope),
                    status_code=500,
                    duration_ms=duration_ms,
                    error_code=type(exc).__name__,
                )
                if response_started:
                    raise
                await send_with_header(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    }
                )
                await send_with_header(
                    {"type": "http.response.body", "body": b"Internal Server Error"}
                )
                return
            duration_ms = int((time.monotonic() - started) * 1000)
            log_event(
                logging.getLogger("literature_agent.api"),
                logging.INFO,
                "request_completed",
                method=scope.get("method"),
                path=_route_path(scope),
                status_code=status_code,
                duration_ms=duration_ms,
            )


def _header_value(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


def _route_path(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "[unmatched]"
