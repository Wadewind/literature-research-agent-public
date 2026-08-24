"""运行实际领域场景与固定组合回归证据；默认离线、零费用。"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

EVAL_DIR = Path(__file__).parent
BACKEND_DIR = EVAL_DIR.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from tests.evaluation.review_metrics import summarize_review_evaluation  # noqa: E402
from tests.evaluation.review_scenarios import evaluate_review_scenario  # noqa: E402

PASSED_RE = re.compile(r"(?P<count>\d+) passed")


def _run_regression(manifest: dict[str, Any]) -> dict[str, Any]:
    nodes = list(manifest["regression_tests"])
    expected = int(manifest["expected_regression_test_count"])
    if len(nodes) != expected or len(set(nodes)) != expected:
        raise ValueError("固定回归节点数量、唯一性与 expected_regression_test_count 不一致")
    started = perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *nodes],
        cwd=BACKEND_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    match = PASSED_RE.search(output)
    passed_count = int(match.group("count")) if match else 0
    gate_passed = completed.returncode == 0 and passed_count == expected
    return {
        "expected_test_count": expected,
        "passed_test_count": passed_count,
        "exit_code": completed.returncode,
        "duration_seconds": round(perf_counter() - started, 6),
        "gate_passed": gate_passed,
        "command": ["python", "-m", "pytest", "-q", *nodes],
    }


def _meets_thresholds(summary: dict[str, Any], thresholds: dict[str, float]) -> bool:
    return all(summary[name] >= threshold for name, threshold in thresholds.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Phase 4 固定 Review 评测")
    parser.add_argument("--json-output", type=Path, help="可选 JSON 报告路径")
    args = parser.parse_args()
    manifest = json.loads(
        (EVAL_DIR / "review_manifest.json").read_text(encoding="utf-8")
    )
    phase2 = json.loads((EVAL_DIR / manifest["corpus_manifest"]).read_text(encoding="utf-8"))
    started_at = datetime.now(UTC)
    scenario_started = perf_counter()
    evaluations = [
        evaluate_review_scenario(scenario, phase2["corpus"])
        for scenario in manifest["scenarios"]
    ]
    scenario_duration = perf_counter() - scenario_started
    summary = asdict(summarize_review_evaluation(evaluations))
    quality_gate_passed = _meets_thresholds(summary, manifest["thresholds"])
    regression = _run_regression(manifest)
    report = {
        "schema_version": 2,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "manifest_version": manifest["version"],
        "mode": "deterministic_fake_domain_scenarios_plus_fixed_regression",
        "providers": {"chat": "not_called", "embedding": "not_called", "network": "disabled"},
        "thresholds": manifest["thresholds"],
        "quality_summary": summary,
        "quality_scenarios": [asdict(item) for item in evaluations],
        "quality_scenario_duration_seconds": round(scenario_duration, 6),
        "regression_evidence": regression,
        "gate_passed": quality_gate_passed and regression["gate_passed"],
        "limitations": [
            "质量指标来自实际消费研究问题/语料的生产 Domain Validator 与确定性导出器。",
            "Interrupt/Resume、持久化、终态与重放只属于固定组合回归证据，不折算为质量比例。",
            "本 runner 不是完整 Worker/PG Review E2E，也不产生 Review 业务性能结论。",
            "Fake 场景不判断 Evidence 是否在语义上充分支持 Claim。",
        ],
    }
    for item in evaluations:
        print(
            f"{item.scenario_id}: corpus={item.corpus_count} ready={item.ready_source_count} "
            f"failed={item.failed_source_count} matrix="
            f"{item.validated_matrix_rows}/{item.expected_matrix_rows} mapping="
            f"{item.export_mapping_hits}/{item.export_mapping_targets}"
        )
    print(
        "regression: "
        f"{regression['passed_test_count']}/{regression['expected_test_count']}"
    )
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"JSON 报告：{args.json_output}")
    print("质量汇总：" + json.dumps(summary, ensure_ascii=False))
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
