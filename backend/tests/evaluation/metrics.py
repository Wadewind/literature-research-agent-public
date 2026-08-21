"""Phase 2 固定评测的纯指标逻辑。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CitationTarget:
    """manifest 中一条期望引用目标。"""

    paper_id: str
    pages: tuple[int, ...] = ()


@dataclass(frozen=True)
class LocatedItem:
    """检索结果或实际 Citation 的最小定位信息。"""

    paper_id: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class QuestionEvaluation:
    """一道固定问题的实际评测结果。"""

    question_id: str
    category: str
    expected_status: str
    actual_status: str
    retrieval_hits: int
    retrieval_targets: int
    citation_hits: int
    citation_targets: int
    citation_valid: bool
    scope_valid: bool


@dataclass(frozen=True)
class EvaluationSummary:
    """固定问题集的聚合计数；比例由报告层格式化。"""

    question_count: int
    retrieval_question_hits: int
    retrieval_question_count: int
    retrieval_item_hits: int
    retrieval_item_count: int
    citation_item_hits: int
    citation_item_count: int
    citation_valid_count: int
    status_match_count: int
    answered_status_matches: int
    answered_status_count: int
    insufficient_status_matches: int
    insufficient_status_count: int
    selected_scope_valid_count: int
    selected_scope_count: int


def target_is_covered(target: CitationTarget, items: list[LocatedItem]) -> bool:
    """目标 Paper 的任一期望页与实际定位区间相交即视为覆盖。"""
    for item in items:
        if item.paper_id != target.paper_id:
            continue
        if not target.pages:
            return True
        if item.page_start is None:
            return True
        page_end = item.page_end or item.page_start
        if any(item.page_start <= page <= page_end for page in target.pages):
            return True
    return False


def summarize(results: list[QuestionEvaluation]) -> EvaluationSummary:
    """按 manifest 题型聚合 Recall、Citation 与状态符合度。"""
    answered = [item for item in results if item.expected_status == "answered"]
    insufficient = [
        item for item in results if item.expected_status == "insufficient_evidence"
    ]
    selected_scope = [item for item in results if item.category == "scope_boundary"]
    return EvaluationSummary(
        question_count=len(results),
        retrieval_question_hits=sum(
            item.retrieval_hits == item.retrieval_targets for item in answered
        ),
        retrieval_question_count=len(answered),
        retrieval_item_hits=sum(item.retrieval_hits for item in answered),
        retrieval_item_count=sum(item.retrieval_targets for item in answered),
        citation_item_hits=sum(item.citation_hits for item in answered),
        citation_item_count=sum(item.citation_targets for item in answered),
        citation_valid_count=sum(item.citation_valid for item in results),
        status_match_count=sum(
            item.actual_status == item.expected_status for item in results
        ),
        answered_status_matches=sum(
            item.actual_status == item.expected_status for item in answered
        ),
        answered_status_count=len(answered),
        insufficient_status_matches=sum(
            item.actual_status == item.expected_status for item in insufficient
        ),
        insufficient_status_count=len(insufficient),
        selected_scope_valid_count=sum(item.scope_valid for item in selected_scope),
        selected_scope_count=len(selected_scope),
    )
