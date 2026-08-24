"""驱动正式 API/Worker 的完整离线 Review 性能旅程；不属于普通测试。"""

import argparse
import json
import platform
import threading
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx


def build_loopback_client(base_url: str) -> httpx.Client:
    """创建不继承宿主代理变量的本机基线 Client。"""
    return httpx.Client(base_url=base_url, timeout=10, trust_env=False)


def validate_readiness(payload: Any) -> None:
    """复核生产 `/health/ready` 成功契约。"""
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError("API 尚未 ready")
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict) or any(
        dependencies.get(name) != "ok" for name in ("postgres", "valkey")
    ):
        raise RuntimeError("API 依赖尚未 ready")


class WorkerRssSampler:
    """从新启动 Worker 的 procfs 采样 RSS/HWM，不读取进程环境。"""

    def __init__(self, pid: int | None, interval_seconds: float = 0.05) -> None:
        self._pid = pid
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.max_rss_kib: int | None = None
        self.max_hwm_kib: int | None = None

    def __enter__(self) -> "WorkerRssSampler":
        if self._pid is not None:
            self._sample()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._sample()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._sample()

    def _sample(self) -> None:
        if self._pid is None:
            return
        status_path = Path(f"/proc/{self._pid}/status")
        try:
            values = {
                key.rstrip(":"): int(value)
                for line in status_path.read_text(encoding="utf-8").splitlines()
                if (parts := line.split())
                and (key := parts[0]) in {"VmRSS:", "VmHWM:"}
                and (value := parts[1]).isdigit()
            }
        except FileNotFoundError as exc:
            raise RuntimeError(f"Worker PID {self._pid} 在测量期间退出") from exc
        rss = values.get("VmRSS")
        hwm = values.get("VmHWM")
        if rss is not None:
            self.max_rss_kib = max(self.max_rss_kib or 0, rss)
        if hwm is not None:
            self.max_hwm_kib = max(self.max_hwm_kib or 0, hwm)


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    headers = {"X-Correlation-ID": f"phase4-baseline-{uuid.uuid4()}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    response = client.request(method, path, json=body, headers=headers)
    response.raise_for_status()
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "")
    return response.json() if "json" in content_type else response.content


def _wait_for_status(
    client: httpx.Client,
    base: str,
    expected: str,
    *,
    deadline: float,
    poll_seconds: float,
) -> dict[str, Any]:
    while perf_counter() < deadline:
        detail = _request(client, "GET", base)
        status = detail["run"]["status"]
        if status == expected:
            return detail
        if status in {"failed", "cancelled", "succeeded"}:
            raise RuntimeError(f"等待 {expected} 时 Review 提前进入 {status}")
        time.sleep(poll_seconds)
    raise TimeoutError(f"等待 Review 状态 {expected} 超时")


def _submit_input(
    client: httpx.Client,
    base: str,
    detail: dict[str, Any],
    *,
    action: str,
    payload: dict[str, Any],
) -> None:
    request = detail["open_human_input_request"]
    if request is None:
        raise RuntimeError("waiting_input 缺少 open_human_input_request")
    _request(
        client,
        "POST",
        f"{base}/outline-input",
        body={
            "request_id": request["request_id"],
            "request_version": request["request_version"],
            "outline_output_id": request["outline_output_id"],
            "action": action,
            "payload": payload,
        },
        idempotency_key=f"phase4-baseline-{action}-{uuid.uuid4()}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="运行完整 Fake Review 本机性能基线")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--worker-pid", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--poll-seconds", type=float, default=0.1)
    parser.add_argument("--hitl-delay-seconds", type=float, default=0.5)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.poll_seconds <= 0 or args.hitl_delay_seconds < 0:
        raise ValueError("timeout/poll 必须为正，HITL delay 不得为负")

    with build_loopback_client(args.base_url) as client:
        ready = _request(client, "GET", "/health/ready")
        validate_readiness(ready)
        project = _request(
            client,
            "POST",
            "/api/v1/projects",
            body={"name": f"Phase 4 Review Baseline {uuid.uuid4()}", "description": ""},
        )
        project_id = project["project_id"]
        base = f"/api/v1/projects/{project_id}/reviews"
        deadline = perf_counter() + args.timeout_seconds
        active_seconds = 0.0
        human_pause_seconds = 0.0
        started_at = datetime.now(UTC)
        wall_started = perf_counter()
        with WorkerRssSampler(args.worker_pid) as rss:
            segment_started = perf_counter()
            created = _request(
                client,
                "POST",
                base,
                body={
                    "research_question": (
                        "How do the fixed synthetic studies compare, and what evidence "
                        "and limitations do they report?"
                    )
                },
                idempotency_key=f"phase4-baseline-review-{uuid.uuid4()}",
            )
            run_id = created["run_id"]
            review_base = f"{base}/{run_id}"
            first_wait = _wait_for_status(
                client,
                review_base,
                "waiting_input",
                deadline=deadline,
                poll_seconds=args.poll_seconds,
            )
            active_seconds += perf_counter() - segment_started
            first_request = first_wait["open_human_input_request"]
            time.sleep(args.hitl_delay_seconds)
            human_pause_seconds += args.hitl_delay_seconds
            segment_started = perf_counter()
            _submit_input(
                client,
                review_base,
                first_wait,
                action="feedback",
                payload={"feedback": "Keep the evidence comparison and state limitations."},
            )
            second_wait = _wait_for_status(
                client,
                review_base,
                "waiting_input",
                deadline=deadline,
                poll_seconds=args.poll_seconds,
            )
            active_seconds += perf_counter() - segment_started
            second_request = second_wait["open_human_input_request"]
            if first_request is None or second_request is None:
                raise RuntimeError("两次 HITL 均必须具有开放 Request")
            if first_request["request_id"] == second_request["request_id"]:
                raise RuntimeError("feedback 后未产生新 HumanInput Request")
            time.sleep(args.hitl_delay_seconds)
            human_pause_seconds += args.hitl_delay_seconds
            segment_started = perf_counter()
            _submit_input(client, review_base, second_wait, action="approve", payload={})
            terminal = _wait_for_status(
                client,
                review_base,
                "succeeded",
                deadline=deadline,
                poll_seconds=args.poll_seconds,
            )
            active_seconds += perf_counter() - segment_started
            total_wall_seconds = perf_counter() - wall_started
            sources = _request(client, "GET", f"{review_base}/sources")
            artifacts = _request(client, "GET", f"{review_base}/artifacts")
            events = _request(client, "GET", f"{review_base}/events?limit=500")
            artifact_bytes = 0
            for artifact in artifacts:
                content_response = client.get(
                    f"{review_base}/artifacts/{artifact['artifact_id']}/content",
                    headers={"X-Correlation-ID": f"phase4-baseline-{uuid.uuid4()}"},
                )
                content_response.raise_for_status()
                content = content_response.content
                if not content:
                    raise RuntimeError("Artifact content 为空")
                artifact_bytes += len(content)

        source_statuses: dict[str, int] = {}
        for source in sources:
            status = source["status"]
            source_statuses[status] = source_statuses.get(status, 0) + 1
        step_statuses = {
            step["step_key"]: step["status"] for step in terminal["steps"]
        }
        event_types = [event["event_type"] for event in events]
        required_events = {
            "human_input_requested",
            "human_input_submitted",
            "review_artifact_created",
            "run_succeeded",
        }
        if len(sources) != 4 or source_statuses != {"ready": 3, "failed": 1}:
            raise RuntimeError(f"Demo Source 终态不符合预期: {source_statuses}")
        if len(artifacts) != 6:
            raise RuntimeError(f"Artifact 数量不是 6: {len(artifacts)}")
        if not required_events <= set(event_types):
            raise RuntimeError("缺少 Review 完整旅程关键 Event")
        if event_types.count("human_input_requested") != 2 or event_types.count(
            "human_input_submitted"
        ) != 2:
            raise RuntimeError("完整旅程必须恰好包含两轮 HITL Request/Submit")
        if any(status != "succeeded" for status in step_statuses.values()):
            raise RuntimeError(f"存在非 succeeded Step: {step_statuses}")

    report = {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "mode": "formal_api_pg_valkey_arq_worker_fake_review",
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "httpx": version("httpx"),
            "base_url": args.base_url,
            "worker_pid": args.worker_pid,
            "providers": {
                "parser": "fake",
                "embedding": "fake",
                "chat": "fake",
                "arxiv": "fixture",
                "external_http_calls": 0,
                "paid_provider_calls": 0,
            },
        },
        "measurement": {
            "total_wall_seconds": round(total_wall_seconds, 6),
            "active_processing_seconds": round(active_seconds, 6),
            "automatic_human_pause_seconds": round(human_pause_seconds, 6),
            "poll_interval_seconds": args.poll_seconds,
            "worker_peak_sampled_rss_kib": rss.max_rss_kib,
            "worker_process_vm_hwm_kib": rss.max_hwm_kib,
            "rss_sampling_interval_seconds": 0.05 if args.worker_pid else None,
        },
        "result": {
            "project_id": project_id,
            "run_id": run_id,
            "run_status": terminal["run"]["status"],
            "review_stage": terminal["review"]["current_stage"],
            "source_count": len(sources),
            "source_statuses": source_statuses,
            "artifact_count": len(artifacts),
            "artifact_total_bytes": artifact_bytes,
            "step_statuses": step_statuses,
            "event_count": len(events),
            "event_types": event_types,
            "feedback_request_changed": True,
        },
        "limitations": [
            "HITL 由脚本各等待固定时长后自动提交，不是人工阅读耗时。",
            "active processing 是三段 API 提交到目标状态的 wall 之和，排除固定 HITL pause。",
            "Worker 在旅程前新启动；VmHWM 包含 Worker 启动与完整旅程，不含容器进程内存。",
            "单用户、单 Run、单次本机观察，不构成 SLA 或并发容量结论。",
        ],
    }
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"JSON 报告：{args.json_output}")
    print(json.dumps(report["measurement"], ensure_ascii=False))
    print(json.dumps(report["result"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
