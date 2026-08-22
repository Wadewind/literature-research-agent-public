"""综述 Markdown、首次引用编号和完整引用映射的确定性组装。"""

from dataclasses import dataclass
from typing import Any

from literature_agent.domain.evidence import Citation, Claim, Evidence
from literature_agent.domain.exceptions import ReviewExportInvalidError
from literature_agent.domain.review import ReviewSource, ReviewSourceStatus
from literature_agent.domain.review_section import SectionDraft


@dataclass(frozen=True, slots=True)
class ReviewExport:
    """不依赖模型的最终综述正文及其可追溯引用事实。"""

    markdown: str
    reference_mapping: tuple[dict[str, Any], ...]


def build_review_export(
    *,
    research_question: str,
    sections: list[SectionDraft],
    claims: list[Claim],
    citations: list[Citation],
    evidence: list[Evidence],
    sources: list[ReviewSource],
) -> ReviewExport:
    """按 Section/Claim 顺序分配论文首次引用编号并生成 Markdown。"""
    flattened = [item for section in sections for item in section.claims]
    ordered_claims = sorted(claims, key=lambda item: item.sequence)
    if len(flattened) != len(ordered_claims):
        raise ReviewExportInvalidError("section_claim_set_mismatch")
    for sequence, (draft, claim) in enumerate(zip(flattened, ordered_claims, strict=True), 1):
        if claim.sequence != sequence or claim.text != draft.text:
            raise ReviewExportInvalidError("section_claim_set_mismatch")

    evidence_by_id = {item.evidence_id: item for item in evidence}
    if len(evidence_by_id) != len(evidence):
        raise ReviewExportInvalidError("duplicate_evidence_id")
    source_by_paper: dict[str, ReviewSource] = {}
    for source in sources:
        if (
            source.status is not ReviewSourceStatus.READY
            or source.paper_id is None
            or source.paper_version_id is None
        ):
            continue
        if source.paper_id in source_by_paper:
            raise ReviewExportInvalidError("duplicate_ready_source_paper")
        source_by_paper[source.paper_id] = source

    persisted_by_claim: dict[str, set[str]] = {}
    for citation in citations:
        persisted_by_claim.setdefault(citation.claim_id, set()).add(citation.evidence_id)

    number_by_paper: dict[str, int] = {}
    mapping_by_paper: dict[str, dict[str, Any]] = {}
    body: list[str] = [f"# {research_question.strip()}", ""]
    claim_index = 0
    for section in sections:
        body.extend((f"## {section.title}", "", section.summary, ""))
        if not section.claims:
            body.extend(("证据不足，当前无法形成可引用的章节结论。", ""))
            continue
        for draft in section.claims:
            claim = ordered_claims[claim_index]
            claim_index += 1
            expected_ids = set(draft.evidence_ids)
            if persisted_by_claim.get(claim.claim_id, set()) != expected_ids:
                raise ReviewExportInvalidError("claim_citation_set_mismatch")
            numbers: list[int] = []
            seen_numbers: set[int] = set()
            for evidence_id in draft.evidence_ids:
                item = evidence_by_id.get(evidence_id)
                if item is None:
                    raise ReviewExportInvalidError("citation_evidence_missing")
                source = source_by_paper.get(item.paper_id)
                if source is None or source.paper_version_id != item.version_id:
                    raise ReviewExportInvalidError("citation_source_scope_mismatch")
                number = number_by_paper.setdefault(item.paper_id, len(number_by_paper) + 1)
                mapping = mapping_by_paper.setdefault(
                    item.paper_id,
                    {
                        "number": number,
                        "paper_id": item.paper_id,
                        "paper_version_id": item.version_id,
                        "source_id": source.source_id,
                        "arxiv_id": source.arxiv_id,
                        "arxiv_version": source.arxiv_version,
                        "title": _metadata_text(source, "title", "未命名论文"),
                        "authors": _metadata_authors(source),
                        "published_at": _metadata_text(source, "published_at", ""),
                        "evidence_ids": [],
                        "claim_ids": [],
                        "locators": [],
                    },
                )
                if evidence_id not in mapping["evidence_ids"]:
                    mapping["evidence_ids"].append(evidence_id)
                    mapping["locators"].append(
                        {
                            "evidence_id": evidence_id,
                            "parse_revision_id": item.parse_revision_id,
                            "chunk_id": item.chunk_id,
                            "section_path": item.section_path,
                            "page_start": item.page_start,
                            "page_end": item.page_end,
                        }
                    )
                if claim.claim_id not in mapping["claim_ids"]:
                    mapping["claim_ids"].append(claim.claim_id)
                if number not in seen_numbers:
                    seen_numbers.add(number)
                    numbers.append(number)
            body.extend((f"{draft.text}{''.join(f'[{number}]' for number in numbers)}", ""))

    mappings = tuple(sorted(mapping_by_paper.values(), key=lambda item: item["number"]))
    body.extend(("## References", ""))
    for item in mappings:
        authors = ", ".join(item["authors"]) or "未知作者"
        year = str(item["published_at"])[:4] or "n.d."
        body.append(
            f"{item['number']}. {authors}. {item['title']}. "
            f"arXiv:{item['arxiv_id']}{item['arxiv_version']} ({year})."
        )
    body.append("")
    return ReviewExport("\n".join(body), mappings)


def _metadata_text(source: ReviewSource, key: str, fallback: str) -> str:
    value = source.metadata_snapshot.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _metadata_authors(source: ReviewSource) -> list[str]:
    value = source.metadata_snapshot.get("authors")
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
