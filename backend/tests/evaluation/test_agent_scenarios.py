"""Phase 6 Research Agent 固定离线场景测试。"""

import json
from pathlib import Path

from tests.evaluation.agent_metrics import summarize_agent_evaluation
from tests.evaluation.agent_scenarios import evaluate_agent_manifest

EVAL_DIR = Path(__file__).parent


async def test_fixed_agent_scenarios_execute_production_runtime_and_policy_paths() -> None:
    manifest = json.loads((EVAL_DIR / "agent_manifest.json").read_text(encoding="utf-8"))

    results = await evaluate_agent_manifest(manifest)
    summary = summarize_agent_evaluation(
        results,
        required_categories=frozenset(manifest["required_categories"]),
    )

    assert len(results) == manifest["expected_scenario_count"] == 7
    assert summary.scenario_pass_rate == 1.0
    assert summary.failed_scenarios == ()
    assert all(item.observation["production_path"] for item in results)
    assert {item.category for item in results} == set(manifest["required_categories"])


def test_agent_manifest_is_versioned_unique_and_requires_fail_closed_threshold() -> None:
    manifest = json.loads((EVAL_DIR / "agent_manifest.json").read_text(encoding="utf-8"))
    scenarios = manifest["scenarios"]

    assert manifest["version"] == "phase6-agent-eval.v1"
    assert len(scenarios) == manifest["expected_scenario_count"]
    assert len({item["id"] for item in scenarios}) == len(scenarios)
    assert {item["category"] for item in scenarios} == set(manifest["required_categories"])
    assert manifest["thresholds"] == {"scenario_pass_rate": 1.0}
