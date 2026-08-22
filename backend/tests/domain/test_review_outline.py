import json

import pytest

from literature_agent.domain.review_outline import (
    OutlineValidationError,
    parse_outline_json,
    validate_feedback,
    validate_outline,
)

DIMENSIONS = ("method", "limitations", "evaluation")


def valid_outline() -> dict:
    return {
        "sections": [
            {
                "section_key": "methods",
                "title": "主要方法",
                "purpose": "比较主要方法及其限制",
                "dimension_keys": ["method", "limitations"],
            }
        ]
    }


def test_outline_validator_accepts_canonical_payload() -> None:
    outline = validate_outline(valid_outline(), allowed_dimension_keys=DIMENSIONS)

    assert outline.to_payload() == valid_outline()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.update(extra=True), "outline_schema_invalid"),
        (lambda value: value.update(sections=[]), "outline_section_count_invalid"),
        (
            lambda value: value["sections"][0].update(section_key="not-kebab"),
            "outline_section_key_invalid",
        ),
        (
            lambda value: value["sections"][0].update(dimension_keys=["unknown"]),
            "outline_dimension_invalid",
        ),
        (
            lambda value: value["sections"].append(dict(value["sections"][0])),
            "outline_section_key_duplicate",
        ),
    ],
)
def test_outline_validator_rejects_stable_contract_violations(mutate, code: str) -> None:
    payload = valid_outline()
    mutate(payload)

    with pytest.raises(OutlineValidationError) as exc_info:
        validate_outline(payload, allowed_dimension_keys=DIMENSIONS)

    assert code in {issue.code for issue in exc_info.value.issues}


def test_outline_parser_rejects_oversized_model_output() -> None:
    with pytest.raises(OutlineValidationError, match="outline_output_too_large"):
        parse_outline_json("{" + "x" * (64 * 1024) + "}")


def test_feedback_is_trimmed_and_bounded() -> None:
    assert validate_feedback("  请增加局限性比较  ") == "请增加局限性比较"

    with pytest.raises(OutlineValidationError, match="outline_feedback_invalid"):
        validate_feedback(" ")
    with pytest.raises(OutlineValidationError, match="outline_feedback_invalid"):
        validate_feedback("x" * 4001)


def test_outline_json_round_trip() -> None:
    content = json.dumps(valid_outline(), ensure_ascii=False)
    assert parse_outline_json(content).to_payload() == valid_outline()
