"""Prometheus 指标安全边界测试。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prometheus_client import CollectorRegistry

from literature_agent import metrics as metrics_module
from literature_agent.metrics import Metrics, observe_review_stage


def _sample(metrics: Metrics, name: str, labels: dict[str, str]) -> float:
    value = metrics.registry.get_sample_value(name, labels)
    assert value is not None
    return value


def test_metrics_use_only_normalized_low_cardinality_labels() -> None:
    registry = CollectorRegistry()
    subject = Metrics(registry)
    secret = "owner/run/correlation/model/prompt-secret"

    subject.record_run_started(secret)
    subject.record_run_completed(secret, secret, 1.5)
    subject.record_outbox(secret)
    subject.record_model(secret, secret, 0.5)
    subject.record_retrieval(secret, 0.25, 3)
    subject.record_review_stage(secret, secret)
    rendered = subject.render().decode()

    assert secret not in rendered
    assert 'run_type="unknown"' in rendered
    assert 'operation="unknown"' in rendered
    assert 'scope="unknown"' in rendered
    assert 'stage="unknown"' in rendered
    assert 'status="unknown"' in rendered


def test_metrics_record_expected_counters_histograms_and_active_jobs() -> None:
    subject = Metrics(CollectorRegistry())

    subject.record_run_started("review")
    subject.record_run_completed(
        "review", "retry_scheduled", 2.0, attempt_status="failed"
    )
    subject.record_outbox("dispatched")
    subject.record_model("chat", "failed", 0.2)
    subject.record_retrieval("version_snapshot", 0.1, 5)
    subject.record_review_stage("finalize", "succeeded")
    with subject.active_worker_job():
        assert _sample(subject, "agent_worker_active_jobs", {}) == 1

    assert _sample(subject, "agent_worker_active_jobs", {}) == 0
    assert _sample(subject, "agent_run_started_total", {"run_type": "review"}) == 1
    assert (
        _sample(
            subject,
            "agent_run_completed_total",
            {"run_type": "review", "status": "retry_scheduled"},
        )
        == 1
    )
    assert (
        _sample(
            subject,
            "agent_attempt_total",
            {"run_type": "review", "status": "failed"},
        )
        == 1
    )
    assert (
        _sample(
            subject,
            "agent_retrieval_evidence_count_sum",
            {"scope": "version_snapshot"},
        )
        == 5
    )


def test_metrics_update_failure_does_not_escape(monkeypatch) -> None:
    subject = Metrics(CollectorRegistry())

    monkeypatch.setattr(
        subject.run_started, "labels", lambda *_args: (_ for _ in ()).throw(RuntimeError)
    )
    monkeypatch.setattr(metrics_module.logger, "warning", Mock(side_effect=RuntimeError))

    subject.record_run_started("review")

    class _ExplosiveLabel:
        @property
        def value(self):
            raise RuntimeError("不得逃逸")

    subject.record_run_completed(_ExplosiveLabel(), _ExplosiveLabel(), 1.0)
    subject.record_model(_ExplosiveLabel(), _ExplosiveLabel(), 1.0)
    subject.record_retrieval(_ExplosiveLabel(), 1.0, 1)


async def test_review_stage_observer_preserves_success_and_failure(monkeypatch) -> None:
    recorder = Mock()
    monkeypatch.setattr(metrics_module, "metrics", recorder)

    async def succeed() -> str:
        return "ok"

    async def fail() -> str:
        raise ValueError("业务错误")

    assert await observe_review_stage("finalize", succeed()) == "ok"
    with pytest.raises(ValueError, match="业务错误"):
        await observe_review_stage("finalize", fail())

    assert recorder.record_review_stage.call_args_list[0].args == (
        "finalize",
        "succeeded",
    )
    assert recorder.record_review_stage.call_args_list[1].args == (
        "finalize",
        "failed",
    )


def test_worker_metrics_server_is_loopback_and_shutdown_is_joined(monkeypatch) -> None:
    server = SimpleNamespace(shutdown=Mock(), server_close=Mock())
    thread = SimpleNamespace(join=Mock())
    starter = Mock(return_value=(server, thread))
    monkeypatch.setattr(metrics_module, "start_http_server", starter)

    handle = metrics_module.start_worker_metrics_server(8001)
    metrics_module.stop_worker_metrics_server(handle)

    assert handle == (server, thread)
    starter.assert_called_once_with(
        8001,
        addr="127.0.0.1",
        registry=metrics_module.metrics.registry,
    )
    server.shutdown.assert_called_once_with()
    server.server_close.assert_called_once_with()
    thread.join.assert_called_once_with(timeout=5)


def test_worker_metrics_server_disable_and_bind_failure_are_isolated(monkeypatch) -> None:
    starter = Mock(side_effect=OSError("address already in use"))
    monkeypatch.setattr(metrics_module, "start_http_server", starter)

    assert metrics_module.start_worker_metrics_server(0) is None
    assert metrics_module.start_worker_metrics_server(8001) is None
    starter.assert_called_once()


def test_worker_metrics_shutdown_attempts_all_cleanup_after_failure(monkeypatch) -> None:
    server = SimpleNamespace(
        shutdown=Mock(side_effect=RuntimeError("shutdown failed")),
        server_close=Mock(),
    )
    thread = SimpleNamespace(join=Mock())
    monkeypatch.setattr(metrics_module.logger, "warning", Mock(side_effect=RuntimeError))

    metrics_module.stop_worker_metrics_server((server, thread))

    server.server_close.assert_called_once_with()
    thread.join.assert_called_once_with(timeout=5)
