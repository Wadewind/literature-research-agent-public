"""测量本机进程内 API 存活端点基线；结果是观察值，不是 SLA。"""

import argparse
import json
import logging
import os
import platform
import resource
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter

from fastapi.testclient import TestClient

from literature_agent.main import create_app


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * percentile) - 1))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Phase 4 API 本机基线")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.requests < 20 or args.warmup < 0:
        raise ValueError("requests 至少为 20，warmup 不得为负数")

    client = TestClient(create_app())
    # 基线排除终端日志 I/O；生产请求日志功能由独立测试覆盖。
    logging.disable(logging.CRITICAL)
    for _ in range(args.warmup):
        response = client.get("/health/live")
        if response.status_code != 200:
            raise RuntimeError("API warmup 未返回 200")
    samples: list[float] = []
    started_at = datetime.now(UTC)
    for _ in range(args.requests):
        started = perf_counter()
        response = client.get("/health/live")
        samples.append((perf_counter() - started) * 1000)
        if response.status_code != 200:
            raise RuntimeError("API 测量请求未返回 200")

    report = {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "method": "FastAPI TestClient in-process GET /health/live; warmup excluded",
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "fastapi": version("fastapi"),
            "starlette": version("starlette"),
            "sqlalchemy": version("sqlalchemy"),
            "psycopg": version("psycopg"),
            "arq": version("arq"),
        },
        "measurements": {
            "requests": args.requests,
            "warmup_requests": args.warmup,
            "success_count": args.requests,
            "latency_ms": {
                "min": round(min(samples), 6),
                "p50": round(median(samples), 6),
                "p95": round(_percentile(samples, 0.95), 6),
                "max": round(max(samples), 6),
            },
            "process_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "limitations": [
            "TestClient 不包含真实 TCP、反向代理或公网延迟。",
            "存活端点不访问 PostgreSQL/Valkey，不能外推业务 API 容量。",
            "结果只描述本机单次进程观察，不构成 SLA。",
        ],
    }
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"JSON 报告：{args.json_output}")
    print(json.dumps(report["measurements"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
