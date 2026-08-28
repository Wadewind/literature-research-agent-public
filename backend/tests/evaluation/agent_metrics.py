"""Phase 6 Research Agent 固定离线评测的 fail-closed 汇总规则。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentScenarioEvaluation:
    """一个由实际 Runtime 或生产策略路径产生的场景观察。"""

    scenario_id: str
    category: str
    checks: dict[str, bool]
    observation: dict[str, Any]

    @property
    def passed(self) -> bool:
        return (
            bool(self.scenario_id)
            and bool(self.category)
            and bool(self.checks)
            and all(type(value) is bool and value for value in self.checks.values())
            and bool(self.observation.get("production_path"))
        )


@dataclass(frozen=True, slots=True)
class AgentEvaluationSummary:
    scenario_count: int
    scenario_pass_rate: float
    failed_scenarios: tuple[str, ...]
    observed_categories: tuple[str, ...]


def summarize_agent_evaluation(
    results: list[AgentScenarioEvaluation],
    *,
    required_categories: frozenset[str],
) -> AgentEvaluationSummary:
    """汇总本次 runner 的实际观察；缺场景、重名或缺类别均拒绝通过。"""
    if not results:
        raise ValueError("Agent 评测至少需要一个场景")
    scenario_ids = [item.scenario_id for item in results]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("Agent 评测 scenario_id 必须唯一")
    observed_categories = {item.category for item in results}
    missing = required_categories - observed_categories
    if missing:
        raise ValueError(f"Agent 评测缺少必需类别：{', '.join(sorted(missing))}")
    return AgentEvaluationSummary(
        scenario_count=len(results),
        scenario_pass_rate=sum(item.passed for item in results) / len(results),
        failed_scenarios=tuple(item.scenario_id for item in results if not item.passed),
        observed_categories=tuple(sorted(observed_categories)),
    )
