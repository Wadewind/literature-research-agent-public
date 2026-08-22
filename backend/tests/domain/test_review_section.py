import json

import pytest

from literature_agent.domain.review_section import (
    CONSISTENCY_MAX_MODEL_OUTPUT_BYTES,
    SECTION_MAX_MODEL_OUTPUT_BYTES,
    ConsistencyReportValidationError,
    SectionDraftValidationError,
    parse_consistency_report_json,
    parse_section_draft_json,
    validate_consistency_report,
    validate_section_draft,
)


def test_section_v1_accepts_answered_claims_and_bounded_terms() -> None:
    draft = validate_section_draft(
        parse_section_draft_json(
            json.dumps(
                {
                    "section_key": "methods",
                    "title": "方法",
                    "status": "answered",
                    "summary": "比较两类方法。",
                    "claims": [{"text": "方法 A 更节省搜索空间。", "evidence_ids": ["ev-1"]}],
                    "terminology": [{"term": "搜索空间", "definition": "候选状态集合"}],
                }
            )
        ),
        expected_section_key="methods",
        expected_title="方法",
        allowed_evidence_ids={"ev-1"},
    )

    assert draft.claims[0].evidence_ids == ("ev-1",)
    assert draft.to_payload()["status"] == "answered"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "section_key": "methods",
            "title": "方法",
            "status": "answered",
            "summary": "摘要",
            "claims": [{"text": "无引用", "evidence_ids": []}],
            "terminology": [],
        },
        {
            "section_key": "methods",
            "title": "方法",
            "status": "answered",
            "summary": "摘要",
            "claims": [{"text": "伪造", "evidence_ids": ["ev-x"]}],
            "terminology": [],
        },
        {
            "section_key": "methods",
            "title": "方法",
            "status": "insufficient_evidence",
            "summary": "证据不足",
            "claims": [{"text": "不应存在", "evidence_ids": ["ev-1"]}],
            "terminology": [],
        },
    ],
)
def test_section_v1_rejects_invalid_claim_binding(payload: dict) -> None:
    with pytest.raises(SectionDraftValidationError):
        validate_section_draft(
            parse_section_draft_json(json.dumps(payload)),
            expected_section_key="methods",
            expected_title="方法",
            allowed_evidence_ids={"ev-1"},
        )


def test_consistency_report_issues_are_valid_non_blocking_results() -> None:
    report = validate_consistency_report(
        parse_consistency_report_json(
            json.dumps(
                {
                    "status": "issues_found",
                    "issues": [
                        {
                            "issue_type": "terminology",
                            "section_keys": ["methods", "results"],
                            "description": "术语定义不一致。",
                        }
                    ],
                }
            )
        ),
        allowed_section_keys=("methods", "results"),
    )
    assert report.status == "issues_found"


def test_consistency_report_rejects_unknown_section() -> None:
    with pytest.raises(ConsistencyReportValidationError):
        validate_consistency_report(
            parse_consistency_report_json(
                '{"status":"issues_found","issues":[{"issue_type":"contradiction",'
                '"section_keys":["unknown"],"description":"冲突"}]}'
            ),
            allowed_section_keys=("methods",),
        )


def test_section_raw_model_output_has_pre_parse_size_limit() -> None:
    oversized = "{" + "x" * SECTION_MAX_MODEL_OUTPUT_BYTES

    with pytest.raises(SectionDraftValidationError, match="大小上限"):
        parse_section_draft_json(oversized)


def test_consistency_raw_model_output_has_pre_parse_size_limit() -> None:
    oversized = "{" + "x" * CONSISTENCY_MAX_MODEL_OUTPUT_BYTES

    with pytest.raises(ConsistencyReportValidationError, match="大小上限"):
        parse_consistency_report_json(oversized)
