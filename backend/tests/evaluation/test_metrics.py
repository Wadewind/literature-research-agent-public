"""Phase 2 固定评测指标口径测试。"""

from tests.evaluation.metrics import (
    CitationTarget,
    LocatedItem,
    QuestionEvaluation,
    summarize,
    target_is_covered,
)


def test_target_coverage_requires_paper_and_overlapping_page() -> None:
    """同 Paper 且页码区间相交才命中；未知页码保守视为命中。"""
    target = CitationTarget("paper-a", (2, 3))

    assert target_is_covered(target, [LocatedItem("paper-a", 1, 2)])
    assert target_is_covered(target, [LocatedItem("paper-a", None, None)])
    assert not target_is_covered(target, [LocatedItem("paper-a", 4, 5)])
    assert not target_is_covered(target, [LocatedItem("paper-b", 2, 3)])


def test_summary_separates_retrieval_status_and_scope_metrics() -> None:
    """Recall 只统计 answered 题，状态与 selected scope 独立聚合。"""
    results = [
        QuestionEvaluation(
            question_id="q1",
            category="single_paper_fact",
            expected_status="answered",
            actual_status="answered",
            retrieval_hits=1,
            retrieval_targets=1,
            citation_hits=1,
            citation_targets=1,
            citation_valid=True,
            scope_valid=True,
        ),
        QuestionEvaluation(
            question_id="q2",
            category="cross_paper_synthesis",
            expected_status="answered",
            actual_status="answered",
            retrieval_hits=1,
            retrieval_targets=2,
            citation_hits=0,
            citation_targets=2,
            citation_valid=True,
            scope_valid=True,
        ),
        QuestionEvaluation(
            question_id="q3",
            category="scope_boundary",
            expected_status="insufficient_evidence",
            actual_status="answered",
            retrieval_hits=0,
            retrieval_targets=0,
            citation_hits=0,
            citation_targets=0,
            citation_valid=True,
            scope_valid=True,
        ),
    ]

    summary = summarize(results)

    assert summary.question_count == 3
    assert (summary.retrieval_question_hits, summary.retrieval_question_count) == (1, 2)
    assert (summary.retrieval_item_hits, summary.retrieval_item_count) == (2, 3)
    assert (summary.citation_item_hits, summary.citation_item_count) == (1, 3)
    assert summary.citation_valid_count == 3
    assert summary.status_match_count == 2
    assert (summary.answered_status_matches, summary.answered_status_count) == (2, 2)
    assert (
        summary.insufficient_status_matches,
        summary.insufficient_status_count,
    ) == (0, 1)
    assert (summary.selected_scope_valid_count, summary.selected_scope_count) == (1, 1)
