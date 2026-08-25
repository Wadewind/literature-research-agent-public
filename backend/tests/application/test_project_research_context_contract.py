"""Project Research Context Matrix 聚合闭包与有界输出测试。"""

from copy import deepcopy

import pytest

from literature_agent.application.project_research_context_service import (
    ProjectResearchContextError,
    _select_matrix_rows,
    _validate_matrix_payload,
)


def _row(
    *,
    paper_id: str = "paper-1",
    dimension_key: str = "method",
    status: str = "extracted",
    evidence_ids: list[str] | None = None,
) -> dict:
    resolved = ["evidence-1"] if evidence_ids is None else evidence_ids
    return {
        "paper_id": paper_id,
        "dimension_key": dimension_key,
        "status": status,
        "finding": "发现" if status == "extracted" else None,
        "limitations": "限制" if status == "extracted" else None,
        "evidence_ids": resolved if status == "extracted" else [],
    }


def _payload() -> dict:
    return {
        "rows": [_row()],
        "paper_failures": [],
        "summary": {"valid_papers": 1, "failed_papers": 0},
    }


def test_matrix_rejects_evidence_free_row_outside_snapshot() -> None:
    payload = _payload()
    payload["rows"] = [
        _row(
            paper_id="paper-outside",
            status="insufficient_evidence",
            evidence_ids=[],
        )
    ]

    with pytest.raises(ProjectResearchContextError) as exc_info:
        _validate_matrix_payload(payload, allowed_paper_ids={"paper-1"})

    assert exc_info.value.code == "project_context_matrix_invalid"


@pytest.mark.parametrize(
    ("mutate", "allowed"),
    [
        (
            lambda value: value.update(
                paper_failures=[
                    {
                        "source_id": "source-1",
                        "paper_id": "paper-1",
                        "error_code": "evidence_matrix_invalid",
                    }
                ],
                summary={"valid_papers": 1, "failed_papers": 1},
            ),
            {"paper-1"},
        ),
        (
            lambda value: value.update(
                summary={"valid_papers": 0, "failed_papers": 0}
            ),
            {"paper-1"},
        ),
        (
            lambda value: value["rows"][0].update(
                dimension_key="x" * 65
            ),
            {"paper-1"},
        ),
        (
            lambda value: value["rows"][0].update(
                evidence_ids=["e" * 256]
            ),
            {"paper-1"},
        ),
    ],
)
def test_matrix_rejects_inconsistent_or_unbounded_aggregate(mutate, allowed) -> None:
    payload = deepcopy(_payload())
    mutate(payload)

    with pytest.raises(ProjectResearchContextError) as exc_info:
        _validate_matrix_payload(payload, allowed_paper_ids=allowed)

    assert exc_info.value.code == "project_context_matrix_invalid"


def test_matrix_selection_is_stable_bounded_and_reports_only_selected_rows() -> None:
    rows = [
        _row(
            dimension_key=f"dimension_{index:02d}",
            evidence_ids=[f"evidence-{index:02d}"],
        )
        for index in reversed(range(14))
    ]

    selected = _select_matrix_rows(rows)

    assert len(selected) == 12
    assert [item["dimension_key"] for item in selected] == [
        f"dimension_{index:02d}" for index in range(12)
    ]
    assert {
        evidence_id for item in selected for evidence_id in item["evidence_ids"]
    } == {f"evidence-{index:02d}" for index in range(12)}
