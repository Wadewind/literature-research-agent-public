"""Phase 4 固定 Review 领域场景的纯汇总逻辑。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewScenarioEvaluation:
    """一个实际消费研究问题和语料的领域场景结果。"""

    scenario_id: str
    corpus_count: int
    ready_source_count: int
    failed_source_count: int
    expected_matrix_rows: int
    validated_matrix_rows: int
    evidence_scope_targets: int
    evidence_scope_hits: int
    citation_scope_targets: int
    citation_scope_hits: int
    export_mapping_targets: int
    export_mapping_hits: int
    fabricated_evidence_rejected: bool
    research_question_used: bool

    @property
    def passed(self) -> bool:
        mapping_valid = (
            self.export_mapping_targets == 0
            or self.export_mapping_hits == self.export_mapping_targets
        )
        return (
            self.corpus_count >= 3
            and self.ready_source_count + self.failed_source_count == self.corpus_count
            and self.validated_matrix_rows == self.expected_matrix_rows
            and self.evidence_scope_hits == self.evidence_scope_targets
            and self.citation_scope_hits == self.citation_scope_targets
            and mapping_valid
            and self.fabricated_evidence_rejected
            and self.research_question_used
        )


@dataclass(frozen=True)
class ReviewEvaluationSummary:
    scenario_count: int
    scenario_pass_rate: float
    citation_scope_validity: float
    export_citation_mapping_completeness: float
    evidence_scope_closure_rate: float
    fabricated_evidence_rejection_rate: float
    failed_scenarios: tuple[str, ...]


def summarize_review_evaluation(
    results: list[ReviewScenarioEvaluation],
) -> ReviewEvaluationSummary:
    """从实际事实计数汇总；空场景或零映射目标不得产生漂亮比例。"""
    if not results:
        raise ValueError("Review 评测至少需要一个场景")
    mapping_targets = sum(item.export_mapping_targets for item in results)
    if mapping_targets == 0:
        raise ValueError("Review 评测至少需要一个 Artifact mapping 目标")
    closure_targets = sum(item.evidence_scope_targets for item in results)
    if closure_targets == 0:
        raise ValueError("Review 评测至少需要一个 Evidence scope 闭包目标")
    citation_targets = sum(item.citation_scope_targets for item in results)
    if citation_targets == 0:
        raise ValueError("Review 评测至少需要一个 Citation scope 目标")
    return ReviewEvaluationSummary(
        scenario_count=len(results),
        scenario_pass_rate=sum(item.passed for item in results) / len(results),
        citation_scope_validity=sum(item.citation_scope_hits for item in results)
        / citation_targets,
        export_citation_mapping_completeness=sum(
            item.export_mapping_hits for item in results
        )
        / mapping_targets,
        evidence_scope_closure_rate=sum(item.evidence_scope_hits for item in results)
        / closure_targets,
        fabricated_evidence_rejection_rate=sum(
            item.fabricated_evidence_rejected for item in results
        )
        / len(results),
        failed_scenarios=tuple(item.scenario_id for item in results if not item.passed),
    )
