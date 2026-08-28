"""Phase 6 Research Agent 离线评测门槛测试。"""

import pytest

from tests.evaluation.agent_metrics import (
    AgentScenarioEvaluation,
    summarize_agent_evaluation,
)


def _passed(scenario_id: str, category: str) -> AgentScenarioEvaluation:
    return AgentScenarioEvaluation(
        scenario_id=scenario_id,
        category=category,
        checks={"actual_path_executed": True, "expected_behavior_observed": True},
        observation={"production_path": "FakeResearchAgentRuntime", "execution_count": 1},
    )


def test_agent_summary_is_fail_closed_for_failed_checks() -> None:
    passed = _passed("multi-turn", "multi_turn_goal")
    failed = AgentScenarioEvaluation(
        scenario_id="scope",
        category="project_matrix_scope",
        checks={"actual_path_executed": True, "scope_frozen": False},
        observation={"project_ref_count": 1},
    )

    summary = summarize_agent_evaluation(
        [passed, failed],
        required_categories=frozenset({"multi_turn_goal", "project_matrix_scope"}),
    )

    assert summary.scenario_count == 2
    assert summary.scenario_pass_rate == 0.5
    assert summary.failed_scenarios == ("scope",)


def test_agent_summary_rejects_missing_or_duplicate_evidence() -> None:
    with pytest.raises(ValueError, match="至少需要一个场景"):
        summarize_agent_evaluation([], required_categories=frozenset())
    duplicate = _passed("same", "multi_turn_goal")
    with pytest.raises(ValueError, match="scenario_id"):
        summarize_agent_evaluation(
            [duplicate, duplicate], required_categories=frozenset({"multi_turn_goal"})
        )
    with pytest.raises(ValueError, match="缺少必需类别"):
        summarize_agent_evaluation(
            [duplicate],
            required_categories=frozenset({"multi_turn_goal", "cancel_resume"}),
        )
