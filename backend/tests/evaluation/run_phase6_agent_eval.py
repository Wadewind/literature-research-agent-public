"""运行 Phase 6 固定 Research Agent 评测；默认离线、零费用。"""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from tests.evaluation.agent_metrics import summarize_agent_evaluation
from tests.evaluation.agent_scenarios import evaluate_agent_manifest

EVAL_DIR = Path(__file__).parent


async def _run(json_output: Path | None) -> int:
    manifest = json.loads((EVAL_DIR / "agent_manifest.json").read_text(encoding="utf-8"))
    started_at = datetime.now(UTC)
    started = perf_counter()
    evaluations = await evaluate_agent_manifest(manifest)
    summary = summarize_agent_evaluation(
        evaluations,
        required_categories=frozenset(manifest["required_categories"]),
    )
    threshold = float(manifest["thresholds"]["scenario_pass_rate"])
    gate_passed = (
        len(evaluations) == int(manifest["expected_scenario_count"])
        and summary.scenario_pass_rate >= threshold
        and not summary.failed_scenarios
    )
    report = {
        "schema_version": 1,
        "manifest_version": manifest["version"],
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(perf_counter() - started, 6),
        "mode": "deterministic_fake_runtime_plus_production_policy_paths",
        "providers": {"chat": "not_called", "network": "disabled"},
        "thresholds": manifest["thresholds"],
        "summary": asdict(summary),
        "scenarios": [asdict(item) for item in evaluations],
        "gate_passed": gate_passed,
        "limitations": [
            "Fake Runtime 验证平台契约与确定性行为，不评判真实模型回答的语义质量。",
            "来源场景验证正式 URL 规范化与候选 Artifact 小事实，不访问实时网站。",
            "故障、越权与持久化边界由 Phase 6 固定回归套件补充验证。",
        ],
    }
    for item in evaluations:
        print(f"{item.scenario_id}: {'passed' if item.passed else 'failed'}")
    print(f"summary: {summary.scenario_count} scenarios, rate={summary.scenario_pass_rate:.3f}")
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"JSON 报告：{json_output}")
    return 0 if gate_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Phase 6 Research Agent 离线评测")
    parser.add_argument("--json-output", type=Path, help="可选 JSON 报告路径")
    args = parser.parse_args()
    return asyncio.run(_run(args.json_output))


if __name__ == "__main__":
    raise SystemExit(main())
