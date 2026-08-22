import hashlib

import pytest

from literature_agent.domain.arxiv import (
    ArxivQueryValidationError,
    ArxivSearchQuery,
    DownloadedPdf,
    parse_versioned_arxiv_id,
)


def test_query_normalizes_whitespace_and_keeps_allowed_fields() -> None:
    query = ArxivSearchQuery("  ti:agents   AND   cat:cs.AI  ", max_results=3)

    assert query.expression == "ti:agents AND cat:cs.AI"


@pytest.mark.parametrize(
    "expression,code",
    [
        ("https://evil.example/query", "arxiv_query_url_forbidden"),
        ("doi:10.1/example", "arxiv_query_field_forbidden"),
        ("all:agent\ncat:cs.AI", "arxiv_query_control_character"),
        (" ", "arxiv_query_invalid_length"),
    ],
)
def test_query_rejects_unsafe_or_unsupported_expression(expression: str, code: str) -> None:
    with pytest.raises(ArxivQueryValidationError, match=code):
        ArxivSearchQuery(expression)


def test_parse_modern_and_legacy_versioned_ids() -> None:
    assert parse_versioned_arxiv_id("https://arxiv.org/abs/2401.12345v2") == (
        "2401.12345",
        "v2",
    )
    assert parse_versioned_arxiv_id("hep-th/9901001v1") == ("hep-th/9901001", "v1")


@pytest.mark.parametrize(
    ("content", "content_hash", "media_type", "code"),
    [
        (
            b"not-a-pdf",
            hashlib.sha256(b"not-a-pdf").hexdigest(),
            "application/pdf",
            "downloaded_pdf_magic_invalid",
        ),
        (
            b"%PDF-document",
            "0" * 64,
            "application/pdf",
            "downloaded_pdf_hash_mismatch",
        ),
        (
            b"%PDF-document",
            hashlib.sha256(b"%PDF-document").hexdigest(),
            "text/plain",
            "downloaded_pdf_media_type_invalid",
        ),
    ],
)
def test_downloaded_pdf_direct_construction_enforces_verified_content(
    content: bytes,
    content_hash: str,
    media_type: str,
    code: str,
) -> None:
    with pytest.raises(ValueError, match=code):
        DownloadedPdf(
            content=content,
            content_hash=content_hash,
            media_type=media_type,
        )
