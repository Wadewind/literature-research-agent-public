"""Citation Validator 领域规则测试（切片 7）。

覆盖确定性校验的全部规则：状态与 claims 一致性、段落级 Claim 严格
绑定 Evidence、伪造/跨 Run/缺失 Evidence ID 拒绝、同一 Claim 内重复
引用拒绝。Validator 是纯函数，不访问数据库。
"""

from dataclasses import replace

from literature_agent.domain.answer_schema import ClaimDraft, RagAnswerOutput
from literature_agent.domain.citation_validator import (
    CitationFailureReason,
    validate_citations,
)
from literature_agent.domain.evidence import AnswerStatus, create_evidence

_RUN_ID = "run-1"


def _evidence(evidence_id: str, *, run_id: str = _RUN_ID):
    """构造一条指定 ID、属于指定 Run 的 Evidence。"""
    return replace(
        create_evidence(
            run_id=run_id,
            project_id="proj-1",
            paper_id="paper-1",
            version_id="version-1",
            parse_revision_id="rev-1",
            chunk_id=f"chunk-{evidence_id}",
            section_path="1 Intro",
            page_start=1,
            page_end=2,
            excerpt=f"摘录 {evidence_id}",
        ),
        evidence_id=evidence_id,
    )


def _output(status: AnswerStatus, claims: list[ClaimDraft]) -> RagAnswerOutput:
    """构造结构化输出。"""
    return RagAnswerOutput(answer_status=status, claims=claims)


def test_valid_answered_output_passes() -> None:
    """answered：每个 Claim 至少一个本次 Run 的 Evidence，校验通过。"""
    evidence = [_evidence("e1"), _evidence("e2")]
    output = _output(
        AnswerStatus.ANSWERED,
        [
            ClaimDraft(text="论述一", evidence_ids=["e1"]),
            ClaimDraft(text="论述二", evidence_ids=["e1", "e2"]),
        ],
    )

    result = validate_citations(output, evidence=evidence, run_id=_RUN_ID)

    assert result.passed
    assert result.failures == []


def test_valid_insufficient_evidence_output_passes() -> None:
    """insufficient_evidence：claims 为空时校验通过。"""
    output = _output(AnswerStatus.INSUFFICIENT_EVIDENCE, [])

    result = validate_citations(output, evidence=[], run_id=_RUN_ID)

    assert result.passed


def test_answered_with_empty_claims_rejected() -> None:
    """answered 但 claims 为空：empty_claims。"""
    output = _output(AnswerStatus.ANSWERED, [])

    result = validate_citations(output, evidence=[_evidence("e1")], run_id=_RUN_ID)

    assert not result.passed
    assert [f.reason for f in result.failures] == [CitationFailureReason.EMPTY_CLAIMS]


def test_answered_with_uncited_claim_rejected() -> None:
    """answered 状态下零引用 Claim 直接判非法：uncited_claim（严格策略）。"""
    output = _output(
        AnswerStatus.ANSWERED,
        [
            ClaimDraft(text="有引用", evidence_ids=["e1"]),
            ClaimDraft(text="无引用", evidence_ids=[]),
        ],
    )

    result = validate_citations(output, evidence=[_evidence("e1")], run_id=_RUN_ID)

    assert not result.passed
    assert [f.reason for f in result.failures] == [CitationFailureReason.UNCITED_CLAIM]
    assert result.failures[0].claim_index == 1


def test_insufficient_evidence_with_claims_rejected() -> None:
    """insufficient_evidence 但 claims 非空：status_mismatch。"""
    output = _output(
        AnswerStatus.INSUFFICIENT_EVIDENCE,
        [ClaimDraft(text="论述", evidence_ids=["e1"])],
    )

    result = validate_citations(output, evidence=[_evidence("e1")], run_id=_RUN_ID)

    assert not result.passed
    assert [f.reason for f in result.failures] == [CitationFailureReason.STATUS_MISMATCH]


def test_fabricated_evidence_id_rejected() -> None:
    """引用不存在于本次 Run Evidence 集合的 ID（伪造/缺失）：fabricated_evidence。"""
    output = _output(
        AnswerStatus.ANSWERED,
        [ClaimDraft(text="论述", evidence_ids=["e1", "ghost"])],
    )

    result = validate_citations(output, evidence=[_evidence("e1")], run_id=_RUN_ID)

    assert not result.passed
    assert [f.reason for f in result.failures] == [
        CitationFailureReason.FABRICATED_EVIDENCE
    ]


def test_cross_run_evidence_rejected() -> None:
    """Evidence 属于其他 Run（链完整性复核失败）：cross_run_evidence。"""
    foreign = _evidence("e1", run_id="run-other")
    output = _output(
        AnswerStatus.ANSWERED,
        [ClaimDraft(text="论述", evidence_ids=["e1"])],
    )

    result = validate_citations(output, evidence=[foreign], run_id=_RUN_ID)

    assert not result.passed
    assert [f.reason for f in result.failures] == [
        CitationFailureReason.CROSS_RUN_EVIDENCE
    ]


def test_duplicate_citation_within_claim_rejected() -> None:
    """同一 Claim 内重复引用同一 Evidence：duplicate_citation（严格拒绝）。"""
    output = _output(
        AnswerStatus.ANSWERED,
        [ClaimDraft(text="论述", evidence_ids=["e1", "e1"])],
    )

    result = validate_citations(output, evidence=[_evidence("e1")], run_id=_RUN_ID)

    assert not result.passed
    assert [f.reason for f in result.failures] == [
        CitationFailureReason.DUPLICATE_CITATION
    ]


def test_same_evidence_shared_across_claims_allowed() -> None:
    """不同 Claim 引用同一 Evidence 合法（只有 Claim 内重复被拒绝）。"""
    output = _output(
        AnswerStatus.ANSWERED,
        [
            ClaimDraft(text="论述一", evidence_ids=["e1"]),
            ClaimDraft(text="论述二", evidence_ids=["e1"]),
        ],
    )

    result = validate_citations(output, evidence=[_evidence("e1")], run_id=_RUN_ID)

    assert result.passed


def test_multiple_failures_collected_in_order() -> None:
    """多个违规按 Claim 顺序全部收集，reason 稳定可供事件与日志使用。"""
    output = _output(
        AnswerStatus.ANSWERED,
        [
            ClaimDraft(text="无引用", evidence_ids=[]),
            ClaimDraft(text="伪造引用", evidence_ids=["ghost"]),
        ],
    )

    result = validate_citations(output, evidence=[], run_id=_RUN_ID)

    assert not result.passed
    assert [f.reason for f in result.failures] == [
        CitationFailureReason.UNCITED_CLAIM,
        CitationFailureReason.FABRICATED_EVIDENCE,
    ]
    assert [f.claim_index for f in result.failures] == [0, 1]
