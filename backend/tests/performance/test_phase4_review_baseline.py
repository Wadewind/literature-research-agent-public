"""完整 Review 手动基线 runner 的离线边界测试。"""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import run_phase4_review_baseline as baseline  # noqa: E402


def test_loopback_client_does_not_inherit_proxy_environment(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class ClientStub:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(baseline.httpx, "Client", ClientStub)

    baseline.build_loopback_client("http://127.0.0.1:8000")

    assert captured == {
        "base_url": "http://127.0.0.1:8000",
        "timeout": 10,
        "trust_env": False,
    }


def test_readiness_matches_production_health_contract() -> None:
    baseline.validate_readiness(
        {"status": "ok", "dependencies": {"postgres": "ok", "valkey": "ok"}}
    )

    with pytest.raises(RuntimeError, match="尚未 ready"):
        baseline.validate_readiness(
            {"status": "ready", "dependencies": {"postgres": "ok", "valkey": "ok"}}
        )
    with pytest.raises(RuntimeError, match="依赖尚未 ready"):
        baseline.validate_readiness(
            {"status": "ok", "dependencies": {"postgres": "ok", "valkey": "failed"}}
        )
