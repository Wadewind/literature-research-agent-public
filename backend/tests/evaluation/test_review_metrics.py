"""Phase 4 Review 实际领域场景与汇总规则测试。"""

import json
from pathlib import Path

import pytest

from tests.evaluation.review_metrics import (
    ReviewScenarioEvaluation,
    summarize_review_evaluation,
)
from tests.evaluation.review_scenarios import evaluate_review_scenario

EVAL_DIR = Path(__file__).parent


def _passed(scenario_id: str) -> ReviewScenarioEvaluation:
    return ReviewScenarioEvaluation(
        scenario_id=scenario_id,
        corpus_count=3,
        ready_source_count=3,
        failed_source_count=0,
        expected_matrix_rows=9,
        validated_matrix_rows=9,
        evidence_scope_targets=6,
        evidence_scope_hits=6,
        citation_scope_targets=2,
        citation_scope_hits=2,
        export_mapping_targets=3,
        export_mapping_hits=3,
        fabricated_evidence_rejected=True,
        research_question_used=True,
    )


def test_review_summary_uses_actual_targets_and_hits() -> None:
    failed = _passed("failed")
    failed = ReviewScenarioEvaluation(
        **{**failed.__dict__, "export_mapping_hits": 2}
    )

    summary = summarize_review_evaluation([_passed("passed"), failed])

    assert summary.scenario_pass_rate == 0.5
    assert summary.export_citation_mapping_completeness == 5 / 6
    assert summary.evidence_scope_closure_rate == 1.0
    assert summary.failed_scenarios == ("failed",)


def test_empty_or_targetless_evaluation_is_rejected() -> None:
    with pytest.raises(ValueError, match="至少需要一个场景"):
        summarize_review_evaluation([])
    item = ReviewScenarioEvaluation(
        **{
            **_passed("targetless").__dict__,
            "export_mapping_targets": 0,
            "export_mapping_hits": 0,
        }
    )
    with pytest.raises(ValueError, match="Artifact mapping"):
        summarize_review_evaluation([item])


def test_review_manifest_reuses_phase2_corpus_and_fixes_regression_nodes() -> None:
    review = json.loads((EVAL_DIR / "review_manifest.json").read_text(encoding="utf-8"))
    phase2 = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    scenarios = review["scenarios"]

    assert len(scenarios) >= 3
    assert len({item["id"] for item in scenarios}) == len(scenarios)
    assert len({item["research_question"] for item in scenarios}) == len(scenarios)
    assert all(3 <= len(item["corpus"]) <= 5 for item in scenarios)
    assert all(set(item["corpus"]) <= set(phase2["corpus"]) for item in scenarios)
    assert {item["mode"] for item in scenarios} == {"answered", "insufficient_evidence"}
    assert any(item["failed_corpus"] for item in scenarios)
    nodes = review["regression_tests"]
    assert len(nodes) == len(set(nodes)) == review["expected_regression_test_count"]
    assert set(review["thresholds"].values()) == {1.0}


def test_all_fixed_questions_and_corpus_are_consumed_by_production_domain_paths() -> None:
    review = json.loads((EVAL_DIR / "review_manifest.json").read_text(encoding="utf-8"))
    phase2 = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))

    results = [
        evaluate_review_scenario(item, phase2["corpus"])
        for item in review["scenarios"]
    ]
    summary = summarize_review_evaluation(results)

    assert summary.scenario_pass_rate == 1.0
    assert summary.citation_scope_validity == 1.0
    assert summary.export_citation_mapping_completeness == 1.0
    assert summary.evidence_scope_closure_rate == 1.0
    assert summary.fabricated_evidence_rejection_rate == 1.0
    assert [item.corpus_count for item in results] == [3, 4, 3]
    assert [item.failed_source_count for item in results] == [0, 1, 0]
