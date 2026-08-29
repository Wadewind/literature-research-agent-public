import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from literature_agent.application.review_section_service import ReviewSectionService
from literature_agent.domain.evidence import create_evidence
from literature_agent.domain.exceptions import (
    IdempotencyConflictError,
    ReviewCitationInvalidError,
    ReviewSectionInvalidError,
    ReviewSectionScopeError,
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.model_types import ChatFinishReason, ChatResult, ModelUsage
from literature_agent.domain.paper_version import PaperVersion
from literature_agent.domain.parse_revision import (
    DocumentParseRevision,
    ParseRevisionStatus,
)
from literature_agent.domain.review import (
    ReviewOutputType,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    create_review_output,
    create_review_run,
    create_review_source,
    create_run_step,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from tests.fakes.fake_claim_set_repository import FakeClaimSetRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_evidence_repository import FakeEvidenceRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository


class _Gateway:
    def __init__(
        self,
        responses: list[dict],
        on_call=None,
        finish_reasons: list[ChatFinishReason | None] | None = None,
    ) -> None:
        self.responses = responses
        self.calls = []
        self.on_call = on_call
        self.finish_reasons = finish_reasons or [None] * len(responses)

    async def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        return ChatResult(
            content=json.dumps(self.responses[len(self.calls) - 1], ensure_ascii=False),
            model="fake",
            usage=ModelUsage(),
            finish_reason=self.finish_reasons[len(self.calls) - 1],
        )


class _Notifier:
    def __init__(self):
        self.run_ids = []

    async def notify(self, run_id):
        self.run_ids.append(run_id)

    def subscribe(self, _run_id):
        raise NotImplementedError

    async def aclose(self):
        return None


async def _seed():
    run_repo = FakeRunRepository()
    review_repo = FakeReviewRepository()
    evidence_repo = FakeEvidenceRepository()
    version_repo = FakePaperVersionRepository()
    revision_repo = FakeParseRevisionRepository()
    claim_repo = FakeClaimSetRepository()
    event_repo = FakeEventRepository()
    run = replace(
        create_run("project-1", "user-1", RunType.REVIEW),
        run_id="review-1",
        status=RunStatus.RUNNING,
        event_sequence=1,
    )
    await run_repo.add(run)
    review_repo.authorize_run(run.run_id, "project-1", "user-1")
    review = replace(
        create_review_run(
            run_id=run.run_id,
            research_question="比较 Agent 方法",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={
                "section_draft": "section_draft.v1",
                "consistency_check": "consistency_check.v1",
            },
            config_snapshot={
                "source_limit": 10,
                "section_output_token_limit": 1_234,
                "consistency_output_token_limit": 777,
            },
        ),
        current_stage=ReviewStage.DRAFT_SECTIONS,
    )
    source = create_review_source(
        review_run_id=run.run_id,
        arxiv_id="2401.1",
        arxiv_version="v1",
        rank=1,
        metadata_snapshot={"title": "论文"},
    ).mark_ready("paper-1", "version-1")
    await review_repo.add_source(source)
    version = PaperVersion(
        version_id="version-1",
        paper_id="paper-1",
        owner_id="user-1",
        file_hash="a" * 64,
        storage_key="paper.pdf",
        size_bytes=1,
        content_type="application/pdf",
        created_at=datetime.now(UTC),
        current_parse_revision_id="revision-1",
    )
    await version_repo.add(version)
    await revision_repo.add(
        DocumentParseRevision(
            revision_id="revision-1",
            version_id="version-1",
            parser_name="fake",
            parser_version="1",
            parser_profile_hash="profile",
            status=ParseRevisionStatus.SUCCEEDED,
        )
    )
    evidence = create_evidence(
        run_id=run.run_id,
        project_id="project-1",
        paper_id="paper-1",
        version_id="version-1",
        parse_revision_id="revision-1",
        chunk_id="chunk-1",
        section_path="Methods",
        page_start=1,
        page_end=1,
        excerpt="证据文本",
    )
    await evidence_repo.add_many([evidence])
    matrix = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="evidence-matrix",
        version=1,
        schema_version="evidence-matrix.v1",
        payload={
            "rows": [
                {
                    "paper_id": "paper-1",
                    "dimension_key": "method",
                    "status": "extracted",
                    "finding": "方法 A",
                    "limitations": None,
                    "evidence_ids": [evidence.evidence_id],
                },
                {
                    "paper_id": "paper-1",
                    "dimension_key": "limitations",
                    "status": "insufficient_evidence",
                    "finding": None,
                    "limitations": None,
                    "evidence_ids": [],
                },
            ],
            "paper_failures": [],
            "summary": {"valid_papers": 1, "failed_papers": 0},
        },
        idempotency_key="matrix",
    )
    outline = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.OUTLINE,
        output_key="outline",
        version=1,
        schema_version="outline.v1",
        payload={
            "sections": [
                {
                    "section_key": "methods",
                    "title": "方法",
                    "purpose": "比较方法",
                    "dimension_keys": ["method"],
                },
                {
                    "section_key": "limitations",
                    "title": "限制",
                    "purpose": "总结限制",
                    "dimension_keys": ["limitations"],
                },
            ]
        },
        idempotency_key="outline",
    )
    review = replace(review, current_outline_output_id=outline.output_id)
    await review_repo.add_review_run(review)
    await review_repo.add_output(matrix)
    await review_repo.add_output(outline)
    matrix_step = create_run_step(
        run_id=run.run_id,
        step_key=ReviewStepKey.BUILD_EVIDENCE_MATRIX,
        sequence=6,
        idempotency_key=f"{run.run_id}:build-evidence-matrix:review-evidence-extraction.v1",
    ).start().succeed({"evidence_matrix_output_id": matrix.output_id})
    outline_step = create_run_step(
        run_id=run.run_id,
        step_key=ReviewStepKey.REVIEW_OUTLINE,
        sequence=9,
        idempotency_key=f"{run.run_id}:review-outline",
    ).start().succeed({"outline_output_id": outline.output_id})
    await review_repo.add_step(matrix_step)
    await review_repo.add_step(outline_step)
    return {
        "run_repo": run_repo,
        "review_repo": review_repo,
        "evidence_repo": evidence_repo,
        "version_repo": version_repo,
        "revision_repo": revision_repo,
        "claim_repo": claim_repo,
        "event_repo": event_repo,
        "matrix": matrix,
        "outline": outline,
        "evidence": evidence,
    }


def _service(data, gateway, notifier=None):
    return ReviewSectionService(
        session_factory=fake_session,
        run_repo_factory=lambda _: data["run_repo"],
        review_repo_factory=lambda _: data["review_repo"],
        evidence_repo_factory=lambda _: data["evidence_repo"],
        paper_version_repo_factory=lambda _: data["version_repo"],
        parse_revision_repo_factory=lambda _: data["revision_repo"],
        claim_set_repo_factory=lambda _: data["claim_repo"],
        event_repo_factory=lambda _: data["event_repo"],
        model_gateway=gateway,
        event_notifier=notifier,
    )


async def test_section_context_is_dimension_scoped_then_claimset_and_report_are_idempotent():
    data = await _seed()
    evidence_id = data["evidence"].evidence_id
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [{"text": "方法 A。", "evidence_ids": [evidence_id]}],
                "terminology": [{"term": "Agent", "definition": "智能体"}],
            },
            {
                "section_key": "limitations",
                "title": "限制",
                "status": "insufficient_evidence",
                "summary": "限制证据不足",
                "claims": [],
                "terminology": [],
            },
            {
                "status": "issues_found",
                "issues": [
                    {
                        "issue_type": "redundancy",
                        "section_keys": ["methods", "limitations"],
                        "description": "摘要略有重复。",
                    }
                ],
            },
        ]
    )
    notifier = _Notifier()
    service = _service(data, gateway, notifier)

    drafted = await service.draft_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        correlation_id="draft",
    )
    first_payload = json.loads(gateway.calls[0][0][1].content)
    second_payload = json.loads(gateway.calls[1][0][1].content)
    assert {row["dimension_key"] for row in first_payload["matrix_rows"]} == {"method"}
    assert {row["dimension_key"] for row in second_payload["matrix_rows"]} == {"limitations"}
    assert second_payload["evidence"] == []
    assert second_payload["prior_section_summaries"] == [
        {"section_key": "methods", "summary": "方法摘要"}
    ]
    assert second_payload["terminology"] == {"Agent": "智能体"}
    assert "paper_failures" not in gateway.calls[0][0][1].content
    assert gateway.calls[0][1]["max_tokens"] == 1_234
    assert first_payload["token_budget"] == 1_234

    validated = await service.validate_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        section_output_ids=[item.output_id for item in drafted.outputs],
        correlation_id="validate",
    )
    report = await service.consistency_check(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        section_output_ids=[item.output_id for item in drafted.outputs],
        claim_set_id=validated.claim_set.claim_set_id,
    )
    assert validated.claim_set.run_id == "review-1"
    assert report.payload["status"] == "issues_found"
    assert gateway.calls[2][1]["max_tokens"] == 777
    assert data["review_repo"].review_runs["review-1"].current_stage is ReviewStage.EXPORT_REVIEW
    assert [item.event_type for item in await data["event_repo"].list_by_run("review-1")] == [
        "section_draft_completed",
        "section_draft_completed",
        "citation_validation_completed",
    ]
    assert notifier.run_ids == ["review-1", "review-1", "review-1"]
    with pytest.raises(ReviewSectionScopeError):
        await service.consistency_check(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            section_output_ids=[item.output_id for item in drafted.outputs],
            claim_set_id="foreign-claim-set",
        )

    replay_gateway = _Gateway([])
    replay = _service(data, replay_gateway, notifier)
    drafted_again = await replay.draft_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        correlation_id="draft-replay",
    )
    assert [item.output_id for item in drafted_again.outputs] == [
        item.output_id for item in drafted.outputs
    ]
    assert replay_gateway.calls == []
    replay_validated = await replay.validate_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        section_output_ids=[item.output_id for item in drafted_again.outputs],
        correlation_id="validate-replay",
    )
    assert replay_validated.claim_set.claim_set_id == validated.claim_set.claim_set_id
    assert notifier.run_ids == ["review-1", "review-1", "review-1"]


async def test_matrix_row_must_match_its_ready_source_paper_version_and_revision():
    data = await _seed()
    original = data["evidence"]
    data["evidence_repo"]._evidence[original.evidence_id] = replace(
        original, paper_id="paper-outside"
    )

    with pytest.raises(ReviewSectionScopeError):
        await _service(data, _Gateway([])).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="scope",
        )


async def test_citation_validation_failure_is_persisted_and_observable():
    data = await _seed()
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [
                    {
                        "text": "方法 A。",
                        "evidence_ids": [data["evidence"].evidence_id],
                    }
                ],
                "terminology": [],
            },
            {
                "section_key": "limitations",
                "title": "限制",
                "status": "insufficient_evidence",
                "summary": "限制证据不足",
                "claims": [],
                "terminology": [],
            },
        ]
    )
    service = _service(data, gateway)
    drafted = await service.draft_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        correlation_id="draft",
    )
    first = drafted.outputs[0]
    index = data["review_repo"].outputs.index(first)
    data["review_repo"].outputs[index] = replace(
        first,
        payload={
            **first.payload,
            "claims": [{"text": "伪造引用", "evidence_ids": ["not-visible"]}],
        },
    )

    with pytest.raises(ReviewCitationInvalidError):
        await service.validate_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            section_output_ids=[item.output_id for item in drafted.outputs],
            correlation_id="invalid",
        )

    validate_step = next(
        item
        for item in data["review_repo"].steps
        if item.step_key.value == "validate_sections"
    )
    assert validate_step.status.value == "failed"
    event = (await data["event_repo"].list_by_run("review-1"))[-1]
    assert event.event_type == "citation_validation_completed"
    assert event.payload == {
        "passed": False,
        "failure_reasons": {"fabricated_evidence": 1},
    }


async def test_section_schema_invalid_fails_step_without_output():
    data = await _seed()
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [],
                "terminology": [],
            }
        ]
    )

    with pytest.raises(ReviewSectionInvalidError, match="section_status_claim_conflict"):
        await _service(data, gateway).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="invalid-section",
        )

    step = next(x for x in data["review_repo"].steps if x.step_key is ReviewStepKey.DRAFT_SECTIONS)
    assert step.status is ReviewStepStatus.FAILED
    assert step.error_code == "section_status_claim_conflict"
    assert not any(x.output_type is ReviewOutputType.SECTION for x in data["review_repo"].outputs)


async def test_section_length_finish_reason_is_classified_without_output():
    data = await _seed()
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "看似完整但 Provider 明确报告截断",
                "claims": [
                    {"text": "方法 A。", "evidence_ids": [data["evidence"].evidence_id]}
                ],
                "terminology": [],
            }
        ],
        finish_reasons=[ChatFinishReason.LENGTH],
    )

    with pytest.raises(ReviewSectionInvalidError, match="section_output_truncated"):
        await _service(data, gateway).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="truncated-section",
        )

    step = next(x for x in data["review_repo"].steps if x.step_key is ReviewStepKey.DRAFT_SECTIONS)
    assert step.status is ReviewStepStatus.FAILED
    assert step.error_code == "section_output_truncated"
    assert not any(x.output_type is ReviewOutputType.SECTION for x in data["review_repo"].outputs)


async def test_cancel_after_section_model_return_does_not_persist_output():
    data = await _seed()

    def cancel(_call):
        current = data["run_repo"]._runs["review-1"]
        data["run_repo"]._runs["review-1"] = replace(
            current, status=RunStatus.CANCEL_REQUESTED
        )

    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [
                    {"text": "方法 A。", "evidence_ids": [data["evidence"].evidence_id]}
                ],
                "terminology": [],
            }
        ],
        on_call=cancel,
    )

    with pytest.raises(RunNotFoundError):
        await _service(data, gateway).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="cancel-after-model",
        )

    assert not any(x.output_type is ReviewOutputType.SECTION for x in data["review_repo"].outputs)


async def test_consistency_schema_invalid_fails_step_without_output():
    data = await _seed()
    evidence_id = data["evidence"].evidence_id
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [{"text": "方法 A。", "evidence_ids": [evidence_id]}],
                "terminology": [],
            },
            {
                "section_key": "limitations",
                "title": "限制",
                "status": "insufficient_evidence",
                "summary": "证据不足",
                "claims": [],
                "terminology": [],
            },
            {
                "status": "consistent",
                "issues": [
                    {
                        "issue_type": "redundancy",
                        "section_keys": ["methods", "limitations"],
                        "description": "非法的 consistent issues",
                    }
                ],
            },
        ]
    )
    service = _service(data, gateway)
    drafted = await service.draft_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        correlation_id="draft",
    )
    validated = await service.validate_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        section_output_ids=[x.output_id for x in drafted.outputs],
        correlation_id="validate",
    )

    with pytest.raises(ReviewSectionInvalidError):
        await service.consistency_check(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            section_output_ids=[x.output_id for x in drafted.outputs],
            claim_set_id=validated.claim_set.claim_set_id,
        )

    step = next(
        x for x in data["review_repo"].steps if x.step_key is ReviewStepKey.CONSISTENCY_CHECK
    )
    assert step.status is ReviewStepStatus.FAILED
    assert not any(
        x.output_type is ReviewOutputType.CONSISTENCY_REPORT
        for x in data["review_repo"].outputs
    )


async def test_cancel_after_consistency_model_return_does_not_persist_or_advance():
    data = await _seed()
    evidence_id = data["evidence"].evidence_id

    def cancel(call):
        if call == 3:
            current = data["run_repo"]._runs["review-1"]
            data["run_repo"]._runs["review-1"] = replace(
                current, status=RunStatus.CANCEL_REQUESTED
            )

    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [{"text": "方法 A。", "evidence_ids": [evidence_id]}],
                "terminology": [],
            },
            {
                "section_key": "limitations",
                "title": "限制",
                "status": "insufficient_evidence",
                "summary": "证据不足",
                "claims": [],
                "terminology": [],
            },
            {"status": "consistent", "issues": []},
        ],
        on_call=cancel,
    )
    service = _service(data, gateway)
    drafted = await service.draft_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        correlation_id="draft",
    )
    validated = await service.validate_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        section_output_ids=[x.output_id for x in drafted.outputs],
        correlation_id="validate",
    )

    with pytest.raises(RunNotFoundError):
        await service.consistency_check(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            section_output_ids=[x.output_id for x in drafted.outputs],
            claim_set_id=validated.claim_set.claim_set_id,
        )

    assert (
        data["review_repo"].review_runs["review-1"].current_stage
        is ReviewStage.CONSISTENCY_CHECK
    )
    assert not any(
        x.output_type is ReviewOutputType.CONSISTENCY_REPORT
        for x in data["review_repo"].outputs
    )


@pytest.mark.parametrize(
    "step_key",
    [ReviewStepKey.BUILD_EVIDENCE_MATRIX, ReviewStepKey.REVIEW_OUTLINE],
)
async def test_prerequisite_step_output_refs_are_authoritative(step_key):
    data = await _seed()
    index = next(
        i
        for i, item in enumerate(data["review_repo"].steps)
        if item.step_key is step_key
    )
    data["review_repo"].steps[index] = replace(
        data["review_repo"].steps[index],
        output_refs={
            (
                "evidence_matrix_output_id"
                if step_key is ReviewStepKey.BUILD_EVIDENCE_MATRIX
                else "outline_output_id"
            ): "old-output"
        },
    )

    with pytest.raises(ReviewSectionScopeError):
        await _service(data, _Gateway([])).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="stale-matrix",
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("section_output_token_limit", True),
        ("section_output_token_limit", 0),
        ("section_output_token_limit", 16_001),
        ("section_output_token_limit", "4000"),
        ("consistency_output_token_limit", 8_001),
    ],
)
async def test_output_token_limits_reject_invalid_profile_value(key, value):
    data = await _seed()
    review = data["review_repo"].review_runs["review-1"]
    data["review_repo"].review_runs["review-1"] = replace(
        review,
        config_snapshot={**review.config_snapshot, key: value},
    )

    with pytest.raises(ReviewSectionScopeError):
        await _service(data, _Gateway([])).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="invalid-profile",
        )


async def test_legacy_v1_profile_uses_explicit_section_token_fallback():
    data = await _seed()
    review = data["review_repo"].review_runs["review-1"]
    data["review_repo"].review_runs["review-1"] = replace(
        review, config_snapshot={"source_limit": 10}
    )
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [
                    {"text": "方法 A。", "evidence_ids": [data["evidence"].evidence_id]}
                ],
                "terminology": [],
            },
            {
                "section_key": "limitations",
                "title": "限制",
                "status": "insufficient_evidence",
                "summary": "证据不足",
                "claims": [],
                "terminology": [],
            },
        ]
    )

    await _service(data, gateway).draft_sections(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        approved_outline_output_id=data["outline"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        correlation_id="legacy-profile",
    )

    assert [call[1]["max_tokens"] for call in gateway.calls] == [4_000, 4_000]


async def test_step_replay_rejects_different_input_refs():
    data = await _seed()
    proposed = create_run_step(
        run_id="review-1",
        step_key=ReviewStepKey.DRAFT_SECTIONS,
        sequence=10,
        idempotency_key="review-1:draft_sections",
        input_refs={"outline_output_id": "different"},
    ).start()
    await data["review_repo"].add_step(proposed)

    with pytest.raises(IdempotencyConflictError):
        await _service(data, _Gateway([])).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="conflict",
        )


async def test_persist_section_rejects_full_output_identity_conflict():
    data = await _seed()
    conflicting = create_review_output(
        review_run_id="review-1",
        output_type=ReviewOutputType.SECTION,
        output_key="section:other",
        version=2,
        schema_version="section.v1",
        payload={"different": True},
        idempotency_key="review-1:section:methods:section_draft.v1",
    )
    await data["review_repo"].add_output(conflicting)
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "方法摘要",
                "claims": [
                    {"text": "方法 A。", "evidence_ids": [data["evidence"].evidence_id]}
                ],
                "terminology": [],
            }
        ]
    )

    with pytest.raises(IdempotencyConflictError):
        await _service(data, gateway).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="output-conflict",
        )

    assert not any(
        item.event_type == "section_draft_completed"
        for item in await data["event_repo"].list_by_run("review-1")
    )


async def test_complete_step_after_cancel_does_not_advance():
    data = await _seed()
    step = create_run_step(
        run_id="review-1",
        step_key=ReviewStepKey.DRAFT_SECTIONS,
        sequence=10,
        idempotency_key="review-1:draft_sections",
        input_refs={"outline_output_id": data["outline"].output_id},
    ).start()
    await data["review_repo"].add_step(step)
    current = data["run_repo"]._runs["review-1"]
    data["run_repo"]._runs["review-1"] = replace(current, status=RunStatus.CANCEL_REQUESTED)

    with pytest.raises(RunNotFoundError):
        await _service(data, _Gateway([]))._complete_step_and_stage(
            "review-1",
            "project-1",
            "user-1",
            ReviewStepKey.DRAFT_SECTIONS,
            ReviewStage.VALIDATE_SECTIONS,
            {"section_output_ids": ["section-1"]},
        )

    stored = next(
        x for x in data["review_repo"].steps if x.step_key is ReviewStepKey.DRAFT_SECTIONS
    )
    assert stored.status is ReviewStepStatus.RUNNING
    assert data["review_repo"].review_runs["review-1"].current_stage is ReviewStage.DRAFT_SECTIONS


class _RefusingAdvanceReviewRepository(FakeReviewRepository):
    async def advance_step(self, step, expected_status):
        if step.status is ReviewStepStatus.FAILED:
            return False
        return await super().advance_step(step, expected_status)


async def test_fail_step_condition_conflict_is_not_ignored():
    data = await _seed()
    refusing = _RefusingAdvanceReviewRepository()
    refusing.__dict__.update(data["review_repo"].__dict__)
    data["review_repo"] = refusing
    gateway = _Gateway(
        [
            {
                "section_key": "methods",
                "title": "方法",
                "status": "answered",
                "summary": "非法",
                "claims": [],
                "terminology": [],
            }
        ]
    )

    with pytest.raises(RunConcurrentModificationError):
        await _service(data, gateway).draft_sections(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id=data["outline"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            correlation_id="step-race",
        )
