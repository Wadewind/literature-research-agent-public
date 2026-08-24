"""实际消费固定问题/语料的 Phase 4 Review 领域场景。"""

import json
from dataclasses import replace
from typing import Any

from literature_agent.domain.answer_schema import ClaimDraft, RagAnswerOutput
from literature_agent.domain.citation_validator import validate_citations
from literature_agent.domain.evidence import (
    AnswerStatus,
    Citation,
    create_claim,
    create_evidence,
)
from literature_agent.domain.review import create_review_source
from literature_agent.domain.review_evidence_matrix import (
    AnalysisDimension,
    EvidenceMatrixValidationError,
    validate_evidence_matrix,
)
from literature_agent.domain.review_export import build_review_export
from literature_agent.domain.review_section import (
    parse_section_draft_json,
    validate_section_draft,
)
from tests.evaluation.review_metrics import ReviewScenarioEvaluation

DIMENSIONS = tuple(
    AnalysisDimension(key, name, question)
    for key, name, question in (
        ("method", "Method", "What method is reported?"),
        ("evaluation", "Evaluation", "What evaluation result is reported?"),
        ("limitations", "Limitations", "What limitation is reported?"),
    )
)


def evaluate_review_scenario(
    scenario: dict[str, Any], phase2_corpus: dict[str, Any]
) -> ReviewScenarioEvaluation:
    """用生产 Validator/导出器实际计算一个固定场景的结构质量。"""
    scenario_id = str(scenario["id"])
    question = str(scenario["research_question"])
    corpus_ids = list(scenario["corpus"])
    failed_ids = set(scenario["failed_corpus"])
    run_id = f"run:{scenario_id}"
    project_id = f"project:{scenario_id}"
    sources = []
    evidence = []
    validated_rows = 0
    evidence_scope_targets = 0
    evidence_scope_hits = 0

    for rank, corpus_id in enumerate(corpus_ids, 1):
        info = phase2_corpus[corpus_id]
        paper_id = f"paper:{corpus_id}"
        version_id = f"version:{corpus_id}"
        source = create_review_source(
            review_run_id=run_id,
            arxiv_id=f"2401.{rank:05d}",
            arxiv_version="v1",
            rank=rank,
            metadata_snapshot={
                "title": info["title"],
                "authors": ["Synthetic Author"],
                "published_at": "2024-01-01",
            },
        )
        if corpus_id in failed_ids:
            sources.append(source.mark_failed("fixture_source_unavailable"))
            continue
        sources.append(source.mark_ready(paper_id, version_id))
        fact = info["planted_facts"][0]
        item = replace(
            create_evidence(
                run_id=run_id,
                project_id=project_id,
                paper_id=paper_id,
                version_id=version_id,
                parse_revision_id=f"revision:{corpus_id}",
                chunk_id=f"chunk:{corpus_id}",
                section_path=fact["section"],
                page_start=fact["page"],
                page_end=fact["page"],
                excerpt=fact["fact"],
            ),
            evidence_id=f"evidence:{corpus_id}",
        )
        evidence.append(item)
        insufficient = scenario["mode"] == "insufficient_evidence"
        rows = [
            {
                "paper_id": paper_id,
                "dimension_key": dimension.dimension_key,
                "status": "insufficient_evidence"
                if insufficient or dimension.dimension_key != "method"
                else "extracted",
                "finding": None
                if insufficient or dimension.dimension_key != "method"
                else fact["fact"],
                "limitations": None,
                "evidence_ids": []
                if insufficient or dimension.dimension_key != "method"
                else [item.evidence_id],
            }
            for dimension in DIMENSIONS
        ]
        validated_rows += len(
            validate_evidence_matrix(
                {"rows": rows},
                dimensions=DIMENSIONS,
                paper_id=paper_id,
                version_id=version_id,
                run_id=run_id,
                project_id=project_id,
                allowed_evidence=[item],
            )
        )
        scope_probe_rows = [dict(row) for row in rows]
        scope_probe_rows[0].update(
            status="extracted",
            finding=fact["fact"],
            evidence_ids=[item.evidence_id],
        )
        for wrong_scope in (
            replace(item, project_id="project:outside"),
            replace(item, run_id="run:outside"),
        ):
            evidence_scope_targets += 1
            try:
                validate_evidence_matrix(
                    {"rows": scope_probe_rows},
                    dimensions=DIMENSIONS,
                    paper_id=paper_id,
                    version_id=version_id,
                    run_id=run_id,
                    project_id=project_id,
                    allowed_evidence=[wrong_scope],
                )
            except EvidenceMatrixValidationError:
                evidence_scope_hits += 1

    if scenario["mode"] == "insufficient_evidence":
        output = RagAnswerOutput(
            answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE, claims=[]
        )
        section_payload = {
            "section_key": "findings",
            "title": "Findings",
            "status": "insufficient_evidence",
            "summary": "The fixed corpus does not contain the requested evidence.",
            "claims": [],
            "terminology": [],
        }
    else:
        output = RagAnswerOutput(
            answer_status=AnswerStatus.ANSWERED,
            claims=[
                ClaimDraft(
                    text=f"{item.paper_id} contributes scoped evidence.",
                    evidence_ids=[item.evidence_id],
                )
                for item in evidence
            ],
        )
        section_payload = {
            "section_key": "findings",
            "title": "Findings",
            "status": "answered",
            "summary": "The fixed sources provide scoped synthetic findings.",
            "claims": [claim.model_dump() for claim in output.claims],
            "terminology": [],
        }
    citation_validation = validate_citations(output, evidence=evidence, run_id=run_id)
    scope_probe_output = RagAnswerOutput(
        answer_status=AnswerStatus.ANSWERED,
        claims=[ClaimDraft(text="scope probe", evidence_ids=[evidence[0].evidence_id])],
    )
    cross_run_rejected = not validate_citations(
        scope_probe_output,
        evidence=[replace(evidence[0], run_id="run:outside")],
        run_id=run_id,
    ).passed
    forged = RagAnswerOutput(
        answer_status=AnswerStatus.ANSWERED,
        claims=[ClaimDraft(text="forged", evidence_ids=["evidence:forged"])],
    )
    fabricated_rejected = not validate_citations(
        forged, evidence=evidence, run_id=run_id
    ).passed
    section = validate_section_draft(
        parse_section_draft_json(json.dumps(section_payload)),
        expected_section_key="findings",
        expected_title="Findings",
        allowed_evidence_ids={item.evidence_id for item in evidence},
    )
    claims = [
        replace(
            create_claim("claim-set", index, draft.text),
            claim_id=f"claim:{index}",
        )
        for index, draft in enumerate(section.claims, 1)
    ]
    citations = [
        Citation(claim.claim_id, evidence_id)
        for claim, draft in zip(claims, section.claims, strict=True)
        for evidence_id in draft.evidence_ids
    ]
    export = build_review_export(
        research_question=question,
        sections=[section],
        claims=claims,
        citations=citations,
        evidence=evidence,
        sources=sources,
    )
    mapping_targets = len(evidence) if scenario["mode"] == "answered" else 0
    mapped_papers = {str(item["paper_id"]) for item in export.reference_mapping}
    expected_papers = {item.paper_id for item in evidence}
    mapping_hits = len(mapped_papers & expected_papers)
    return ReviewScenarioEvaluation(
        scenario_id=scenario_id,
        corpus_count=len(corpus_ids),
        ready_source_count=len(evidence),
        failed_source_count=len(failed_ids),
        expected_matrix_rows=len(evidence) * len(DIMENSIONS),
        validated_matrix_rows=validated_rows,
        evidence_scope_targets=evidence_scope_targets,
        evidence_scope_hits=evidence_scope_hits,
        citation_scope_targets=2,
        citation_scope_hits=int(citation_validation.passed) + int(cross_run_rejected),
        export_mapping_targets=mapping_targets,
        export_mapping_hits=mapping_hits,
        fabricated_evidence_rejected=fabricated_rejected,
        research_question_used=export.markdown.startswith(f"# {question}\n"),
    )
