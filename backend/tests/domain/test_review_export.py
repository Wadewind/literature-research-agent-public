"""Markdown 综述与引用映射的确定性导出契约测试。"""

from literature_agent.domain.evidence import AnswerStatus, Citation, Claim, Evidence
from literature_agent.domain.review import ReviewSource, create_review_source
from literature_agent.domain.review_export import build_review_export
from literature_agent.domain.review_section import (
    SectionClaimDraft,
    SectionDraft,
)


def _source(rank: int, paper_id: str, version_id: str) -> ReviewSource:
    return create_review_source(
        review_run_id="review-1",
        arxiv_id=f"2401.0000{rank}",
        arxiv_version="v1",
        rank=rank,
        metadata_snapshot={
            "title": f"论文 {rank}",
            "authors": [f"作者 {rank}"],
            "published_at": f"2024-01-0{rank}",
        },
    ).mark_ready(paper_id, version_id)


def _evidence(evidence_id: str, paper_id: str, version_id: str, page: int) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id="review-1",
        project_id="project-1",
        paper_id=paper_id,
        version_id=version_id,
        parse_revision_id=f"revision-{paper_id}",
        chunk_id=f"chunk-{evidence_id}",
        section_path="Methods",
        page_start=page,
        page_end=page,
        excerpt="受控摘录",
    )


def test_first_citation_order_drives_markdown_and_complete_mapping() -> None:
    sections = [
        SectionDraft(
            "methods",
            "方法",
            AnswerStatus.ANSWERED,
            "方法摘要",
            (
                SectionClaimDraft("先引用第二篇。", ("e2",)),
                SectionClaimDraft("再同时引用第一、第二篇。", ("e1", "e3")),
            ),
            (),
        )
    ]
    claims = [
        Claim("c1", "claim-set-1", 1, "先引用第二篇。"),
        Claim("c2", "claim-set-1", 2, "再同时引用第一、第二篇。"),
    ]
    citations = [Citation("c2", "e3"), Citation("c1", "e2"), Citation("c2", "e1")]
    evidence = [
        _evidence("e1", "paper-1", "version-1", 2),
        _evidence("e2", "paper-2", "version-2", 3),
        _evidence("e3", "paper-2", "version-2", 4),
    ]

    export = build_review_export(
        research_question="如何比较这些方法？",
        sections=sections,
        claims=claims,
        citations=citations,
        evidence=evidence,
        sources=[_source(1, "paper-1", "version-1"), _source(2, "paper-2", "version-2")],
    )

    assert "先引用第二篇。[1]" in export.markdown
    assert "再同时引用第一、第二篇。[2][1]" in export.markdown
    assert export.reference_mapping[0]["paper_id"] == "paper-2"
    assert export.reference_mapping[0]["evidence_ids"] == ["e2", "e3"]
    assert export.reference_mapping[0]["claim_ids"] == ["c1", "c2"]
    assert export.reference_mapping[0]["locators"][0] == {
        "evidence_id": "e2",
        "parse_revision_id": "revision-paper-2",
        "chunk_id": "chunk-e2",
        "section_path": "Methods",
        "page_start": 3,
        "page_end": 3,
    }
    assert export.reference_mapping[1]["paper_version_id"] == "version-1"
    assert export.reference_mapping[1]["evidence_ids"] == ["e1"]
    assert "1. 作者 2. 论文 2. arXiv:2401.00002v1 (2024)." in export.markdown


def test_export_rejects_claim_or_citation_drift() -> None:
    section = SectionDraft(
        "methods",
        "方法",
        AnswerStatus.ANSWERED,
        "摘要",
        (SectionClaimDraft("论述。", ("e1",)),),
        (),
    )
    claim = Claim("c1", "claim-set-1", 1, "被篡改的论述。")

    try:
        build_review_export(
            research_question="问题",
            sections=[section],
            claims=[claim],
            citations=[Citation("c1", "e1")],
            evidence=[_evidence("e1", "paper-1", "version-1", 1)],
            sources=[_source(1, "paper-1", "version-1")],
        )
    except ValueError as exc:
        assert str(exc) == "section_claim_set_mismatch"
    else:  # pragma: no cover
        raise AssertionError("应拒绝 ClaimSet 与 Section Output 漂移")
