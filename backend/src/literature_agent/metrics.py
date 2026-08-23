"""每进程 Prometheus 指标与低基数标签边界。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from contextlib import contextmanager, suppress
from http.server import HTTPServer
from threading import Thread

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

logger = logging.getLogger(__name__)

RUN_DURATION_BUCKETS = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)
OPERATION_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
)
EVIDENCE_COUNT_BUCKETS = (0, 1, 2, 3, 5, 8, 13, 20, 30, 50)

_RUN_TYPES = frozenset({"ingestion", "indexing", "rag_answer", "review"})
_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled", "retry_scheduled", "paused"})
_ATTEMPT_STATUSES = frozenset({"succeeded", "failed", "cancelled", "paused"})
_OUTBOX_STATUSES = frozenset({"dispatched", "failed", "dropped"})
_MODEL_OPERATIONS = frozenset({"embedding", "chat"})
_BINARY_STATUSES = frozenset({"succeeded", "failed"})
_RETRIEVAL_SCOPES = frozenset({"project", "selected_papers", "version_snapshot"})
_REVIEW_STAGES = frozenset(
    {
        "validate_request",
        "formulate_search_strategy",
        "search_arxiv",
        "import_arxiv_papers",
        "wait_for_ingestion",
        "build_evidence_matrix",
        "propose_outline",
        "review_outline",
        "draft_sections",
        "validate_sections",
        "consistency_check",
        "export_review",
        "finalize",
    }
)


def _label(value: object, allowed: frozenset[str]) -> str:
    """把不可信标签归一化到固定 ``unknown``，防止基数失控。"""
    candidate = getattr(value, "value", value)
    return candidate if isinstance(candidate, str) and candidate in allowed else "unknown"


def _warn(event: str) -> None:
    """Metrics 自身的诊断日志也不得破坏业务路径。"""
    with suppress(Exception):
        logger.warning(event)


class Metrics:
    """封装项目指标；每个实例只写入调用方提供的进程内 Registry。"""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self.run_started = Counter(
            "agent_run_started_total",
            "Worker 成功认领的 Run 数量。",
            ("run_type",),
            registry=self.registry,
        )
        self.run_completed = Counter(
            "agent_run_completed_total",
            "已结束 Worker 执行尝试的 Run 数量。",
            ("run_type", "status"),
            registry=self.registry,
        )
        self.run_duration = Histogram(
            "agent_run_duration_seconds",
            "已认领 Run 的 Worker 执行耗时。",
            ("run_type",),
            buckets=RUN_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.attempts = Counter(
            "agent_attempt_total",
            "已结束的 Worker Attempt 数量。",
            ("run_type", "status"),
            registry=self.registry,
        )
        self.outbox_dispatch = Counter(
            "agent_outbox_dispatch_total",
            "Outbox 派发结果数量。",
            ("status",),
            registry=self.registry,
        )
        self.model_requests = Counter(
            "agent_model_request_total",
            "模型请求结果数量。",
            ("operation", "status"),
            registry=self.registry,
        )
        self.model_duration = Histogram(
            "agent_model_duration_seconds",
            "模型请求耗时。",
            ("operation",),
            buckets=OPERATION_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.retrieval_duration = Histogram(
            "agent_retrieval_duration_seconds",
            "成功检索的耗时。",
            ("scope",),
            buckets=OPERATION_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.retrieval_evidence_count = Histogram(
            "agent_retrieval_evidence_count",
            "成功检索返回的 Evidence 候选数量。",
            ("scope",),
            buckets=EVIDENCE_COUNT_BUCKETS,
            registry=self.registry,
        )
        self.review_stages = Counter(
            "agent_review_stage_total",
            "Review Stage 执行尝试结果数量。",
            ("stage", "status"),
            registry=self.registry,
        )
        self.worker_active_jobs = Gauge(
            "agent_worker_active_jobs",
            "当前 Worker 进程内正在执行的 ARQ Job 数量。",
            registry=self.registry,
        )

    @staticmethod
    def _safe(update) -> bool:
        """指标采集失败不得改变业务调用结果。"""
        try:
            update()
            return True
        except Exception:
            _warn("metrics_update_failed")
            return False

    def record_run_started(self, run_type: object) -> None:
        self._safe(lambda: self.run_started.labels(_label(run_type, _RUN_TYPES)).inc())

    def record_run_completed(
        self,
        run_type: object,
        status: object,
        duration_seconds: float,
        *,
        attempt_status: object | None = None,
    ) -> None:
        def update() -> None:
            run_type_label = _label(run_type, _RUN_TYPES)
            status_label = _label(status, _RUN_STATUSES)
            self.run_completed.labels(run_type_label, status_label).inc()
            self.run_duration.labels(run_type_label).observe(max(0.0, duration_seconds))
            self.attempts.labels(
                run_type_label,
                _label(attempt_status or status, _ATTEMPT_STATUSES),
            ).inc()

        self._safe(update)

    def record_outbox(self, status: object) -> None:
        self._safe(lambda: self.outbox_dispatch.labels(_label(status, _OUTBOX_STATUSES)).inc())

    def record_model(self, operation: object, status: object, duration_seconds: float) -> None:
        def update() -> None:
            operation_label = _label(operation, _MODEL_OPERATIONS)
            status_label = _label(status, _BINARY_STATUSES)
            self.model_requests.labels(operation_label, status_label).inc()
            self.model_duration.labels(operation_label).observe(max(0.0, duration_seconds))

        self._safe(update)

    def record_retrieval(self, scope: object, duration_seconds: float, evidence_count: int) -> None:
        def update() -> None:
            scope_label = _label(scope, _RETRIEVAL_SCOPES)
            self.retrieval_duration.labels(scope_label).observe(max(0.0, duration_seconds))
            self.retrieval_evidence_count.labels(scope_label).observe(max(0, evidence_count))

        self._safe(update)

    def record_review_stage(self, stage: object, status: object) -> None:
        self._safe(
            lambda: self.review_stages.labels(
                _label(stage, _REVIEW_STAGES), _label(status, _BINARY_STATUSES)
            ).inc()
        )

    @contextmanager
    def active_worker_job(self):
        """在 Job 生命周期内维护 Gauge，并隔离 Gauge 更新失败。"""
        incremented = self._safe(self.worker_active_jobs.inc)
        try:
            yield
        finally:
            if incremented:
                self._safe(self.worker_active_jobs.dec)

    def render(self) -> bytes:
        """生成当前进程 Registry 的 Prometheus text exposition。"""
        return generate_latest(self.registry)


metrics = Metrics()

MetricsServer = tuple[HTTPServer, Thread]


def start_worker_metrics_server(port: int) -> MetricsServer | None:
    """在 loopback 启动 Worker scrape endpoint；0 表示显式禁用。"""
    if port == 0:
        return None
    try:
        server, thread = start_http_server(
            port,
            addr="127.0.0.1",
            registry=metrics.registry,
        )
    except Exception:
        _warn("worker_metrics_server_start_failed")
        return None
    return server, thread


def stop_worker_metrics_server(handle: MetricsServer | None) -> None:
    """停止 Worker scrape endpoint；清理失败不阻塞 Worker shutdown。"""
    if handle is None:
        return
    server, thread = handle
    for action in (server.shutdown, server.server_close, lambda: thread.join(timeout=5)):
        try:
            action()
        except Exception:
            _warn("worker_metrics_server_stop_failed")


async def observe_review_stage[T](stage: object, operation: Awaitable[T]) -> T:
    """记录一次 Review Stage 尝试，不改变 awaitable 的成功或异常。"""
    try:
        result = await operation
    except BaseException:
        metrics.record_review_stage(stage, "failed")
        raise
    metrics.record_review_stage(stage, "succeeded")
    return result
