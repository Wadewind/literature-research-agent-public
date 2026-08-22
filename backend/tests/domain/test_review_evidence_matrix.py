"""Evidence Matrix 结构与引用范围的确定性校验。"""

import json

import pytest

from literature_agent.domain.evidence import create_evidence
from literature_agent.domain.review_evidence_matrix import (
    MATRIX_EVIDENCE_MAX_ITEMS,
    MATRIX_TEXT_MAX_CHARS,
    AnalysisDimension,
    EvidenceMatrixValidationError,
    validate_evidence_matrix,
)


def _evidence(*, paper_id: str = "paper-1", version_id: str = "version-1"):
    return create_evidence(
        run_id="review-1",
        project_id="project-1",
        paper_id=paper_id,
        version_id=version_id,
        parse_revision_id="revision-1",
        chunk_id=f"chunk-{paper_id}-{version_id}",
        section_path="Methods",
        page_start=1,
        page_end=1,
        excerpt="evidence",
    )


def _dimensions() -> tuple[AnalysisDimension, ...]:
    return (
        AnalysisDimension("method", "方法", "论文使用了什么方法？"),
        AnalysisDimension("limitations", "限制", "论文有哪些限制？"),
        AnalysisDimension("datasets", "数据集", "论文使用了什么数据集？"),
    )


def _insufficient_row(dimension_key: str) -> dict:
    return {
        "paper_id": "paper-1",
        "dimension_key": dimension_key,
        "status": "insufficient_evidence",
        "finding": None,
        "limitations": None,
        "evidence_ids": [],
    }


def test_validator_accepts_extracted_and_insufficient_rows() -> None:
    evidence = _evidence()
    result = validate_evidence_matrix(
        {
            "rows": [
                {
                    "paper_id": "paper-1",
                    "dimension_key": "method",
                    "status": "extracted",
                    "finding": "使用分层检索。",
                    "limitations": None,
                    "evidence_ids": [evidence.evidence_id],
                },
                _insufficient_row("limitations"),
                _insufficient_row("datasets"),
            ]
        },
        dimensions=_dimensions(),
        paper_id="paper-1",
        version_id="version-1",
        run_id="review-1",
        project_id="project-1",
        allowed_evidence=[evidence],
    )
    assert [row.dimension_key for row in result] == ["method", "limitations", "datasets"]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda rows, _: rows.append(dict(rows[0])), "duplicate_dimension"),
        (lambda rows, _: rows.pop(), "dimension_set_mismatch"),
        (lambda rows, _: rows[0].update(paper_id="paper-2"), "paper_scope_mismatch"),
        (lambda rows, _: rows[0].update(evidence_ids=["fabricated"]), "fabricated_evidence"),
        (
            lambda rows, foreign: rows[0].update(evidence_ids=[foreign.evidence_id]),
            "evidence_scope_mismatch",
        ),
        (lambda rows, _: rows[0].update(finding=""), "status_mismatch"),
        (
            lambda rows, _: rows[1].update(finding="伪造结论"),
            "status_mismatch",
        ),
    ],
)
def test_validator_rejects_invalid_matrix(mutate, code: str) -> None:
    evidence = _evidence()
    foreign = _evidence(paper_id="paper-2", version_id="version-2")
    rows = [
        {
            "paper_id": "paper-1",
            "dimension_key": "method",
            "status": "extracted",
            "finding": "使用分层检索。",
            "limitations": None,
            "evidence_ids": [evidence.evidence_id],
        },
        _insufficient_row("limitations"),
        _insufficient_row("datasets"),
    ]
    mutate(rows, foreign)
    with pytest.raises(EvidenceMatrixValidationError) as exc_info:
        validate_evidence_matrix(
            {"rows": rows},
            dimensions=_dimensions(),
            paper_id="paper-1",
            version_id="version-1",
            run_id="review-1",
            project_id="project-1",
            allowed_evidence=[evidence, foreign],
        )
    assert code in {issue.code for issue in exc_info.value.issues}


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "other-run"},
        {"project_id": "other-project"},
        {"version_id": "other-version"},
    ],
)
def test_validator_rejects_evidence_outside_each_scope_axis(changes: dict) -> None:
    evidence = _evidence()
    foreign = create_evidence(
        run_id=changes.get("run_id", "review-1"),
        project_id=changes.get("project_id", "project-1"),
        paper_id="paper-1",
        version_id=changes.get("version_id", "version-1"),
        parse_revision_id="revision-1",
        chunk_id="foreign-chunk",
        section_path=None,
        page_start=None,
        page_end=None,
        excerpt="foreign",
    )
    payload = {
        "rows": [
            {
                "paper_id": "paper-1",
                "dimension_key": "method",
                "status": "extracted",
                "finding": "结论",
                "limitations": None,
                "evidence_ids": [foreign.evidence_id],
            },
            _insufficient_row("limitations"),
            _insufficient_row("datasets"),
        ]
    }
    with pytest.raises(EvidenceMatrixValidationError) as exc_info:
        validate_evidence_matrix(
            payload,
            dimensions=_dimensions(),
            paper_id="paper-1",
            version_id="version-1",
            run_id="review-1",
            project_id="project-1",
            allowed_evidence=[evidence, foreign],
        )
    assert "evidence_scope_mismatch" in {item.code for item in exc_info.value.issues}


def test_validator_rejects_duplicate_evidence_and_text_limit() -> None:
    evidence = _evidence()
    payload = {
        "rows": [
            {
                "paper_id": "paper-1",
                "dimension_key": "method",
                "status": "extracted",
                "finding": "x" * (MATRIX_TEXT_MAX_CHARS + 1),
                "limitations": None,
                "evidence_ids": [evidence.evidence_id, evidence.evidence_id],
            },
            _insufficient_row("limitations"),
            _insufficient_row("datasets"),
        ]
    }
    with pytest.raises(EvidenceMatrixValidationError) as exc_info:
        validate_evidence_matrix(
            payload,
            dimensions=_dimensions(),
            paper_id="paper-1",
            version_id="version-1",
            run_id="review-1",
            project_id="project-1",
            allowed_evidence=[evidence],
        )
    codes = {item.code for item in exc_info.value.issues}
    assert {"duplicate_evidence", "text_limit_exceeded"} <= codes


@pytest.mark.parametrize(
    "row",
    [
        {"paper_id": "paper-1"},
        {
            "paper_id": "paper-1",
            "dimension_key": "method",
            "status": "extracted",
            "finding": "结论",
            "limitations": None,
            "evidence_ids": [],
            "unknown": True,
        },
        {
            "paper_id": 1,
            "dimension_key": "method",
            "status": "extracted",
            "finding": "结论",
            "limitations": None,
            "evidence_ids": [],
        },
        {
            "paper_id": "paper-1",
            "dimension_key": "method",
            "status": 1,
            "finding": "结论",
            "limitations": None,
            "evidence_ids": [],
        },
        {
            "paper_id": "paper-1",
            "dimension_key": "method",
            "status": "extracted",
            "finding": 1,
            "limitations": None,
            "evidence_ids": [],
        },
    ],
)
def test_validator_rejects_unknown_missing_or_wrong_field_types(row: dict) -> None:
    complete = [_insufficient_row("limitations"), _insufficient_row("datasets")]
    with pytest.raises(EvidenceMatrixValidationError) as exc_info:
        validate_evidence_matrix(
            {"rows": [row, *complete]},
            dimensions=_dimensions(),
            paper_id="paper-1",
            version_id="version-1",
            run_id="review-1",
            project_id="project-1",
            allowed_evidence=[],
        )
    assert exc_info.value.issues


def test_maximum_profile_matrix_fits_review_output_budget() -> None:
    """10 篇×6 维的最坏 UTF-8 合法行仍低于聚合 Output 的 256 KiB 上限。"""
    rows = [
        {
            "paper_id": f"paper-{paper}",
            "dimension_key": f"dimension_{dimension}",
            "status": "extracted",
            "finding": "证" * MATRIX_TEXT_MAX_CHARS,
            "limitations": "限" * MATRIX_TEXT_MAX_CHARS,
            "evidence_ids": [
                f"{paper:02d}-{dimension:02d}-{index:032d}"
                for index in range(MATRIX_EVIDENCE_MAX_ITEMS)
            ],
        }
        for paper in range(10)
        for dimension in range(6)
    ]
    payload = {
        "rows": rows,
        "paper_failures": [],
        "summary": {"valid_papers": 10, "failed_papers": 0},
    }

    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 240 * 1024
