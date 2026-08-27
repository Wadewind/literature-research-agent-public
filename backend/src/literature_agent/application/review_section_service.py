"""Phase 3 章节写作、ClaimSet、引用校验与一致性报告。"""

import json
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.claim_set_repository import ClaimSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.paper_version_repository import PaperVersionRepository
from literature_agent.application.ports.parse_revision_repository import ParseRevisionRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.answer_schema import ClaimDraft, RagAnswerOutput
from literature_agent.domain.citation_validator import CitationFailureReason, validate_citations
from literature_agent.domain.event import create_event
from literature_agent.domain.evidence import (
    AnswerStatus,
    Citation,
    Claim,
    ClaimSet,
    Evidence,
    create_claim,
    create_claim_set,
)
from literature_agent.domain.exceptions import (
    IdempotencyConflictError,
    ReviewCitationInvalidError,
    ReviewSectionInvalidError,
    ReviewSectionScopeError,
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.model_types import ChatMessage, ChatResult
from literature_agent.domain.parse_revision import ParseRevisionStatus
from literature_agent.domain.review import (
    ReviewOutput,
    ReviewOutputType,
    ReviewSource,
    ReviewSourceStatus,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    create_review_output,
    create_run_step,
)
from literature_agent.domain.review_outline import validate_outline
from literature_agent.domain.review_section import (
    CONSISTENCY_JSON_SCHEMA,
    SECTION_JSON_SCHEMA,
    ConsistencyReportValidationError,
    SectionDraft,
    SectionDraftValidationError,
    parse_consistency_report_json,
    parse_section_draft_json,
    validate_consistency_report,
    validate_section_draft,
)
from literature_agent.domain.run import RunStatus, RunType

WORKFLOW_VERSION = "review.v1"
SUPPORTED_MODEL_PROFILE_VERSIONS = frozenset(
    {"review-default.v1", "review-default.v2"}
)
SECTION_PROMPT_VERSION = "section_draft.v1"
CONSISTENCY_PROMPT_VERSION = "consistency_check.v1"
SECTION_SCHEMA_VERSION = "section.v1"
CONSISTENCY_SCHEMA_VERSION = "consistency-report.v1"
SECTION_MAX_TOKENS = 4_000
CONSISTENCY_MAX_TOKENS = 2_000
_MIN_OUTPUT_TOKENS = 256
_MAX_SECTION_OUTPUT_TOKENS = 16_000
_MAX_CONSISTENCY_OUTPUT_TOKENS = 8_000
_STAGE_ORDER = {stage: index for index, stage in enumerate(ReviewStage)}


class ReviewSectionModelGateway(Protocol):
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
    ) -> ChatResult: ...


@dataclass(frozen=True, slots=True)
class SectionDraftingResult:
    outputs: tuple[ReviewOutput, ...]
    model_invocations: int


@dataclass(frozen=True, slots=True)
class CitationValidationOutcome:
    claim_set: ClaimSet
    claims: tuple[Claim, ...]
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class _SectionSpec:
    section_key: str
    title: str
    purpose: str
    dimension_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ReviewContext:
    question: str
    outline_id: str
    matrix_id: str
    sections: tuple[_SectionSpec, ...]
    matrix_rows: tuple[dict, ...]
    evidence_by_id: dict[str, Evidence]
    source_by_paper: dict[str, ReviewSource]
    outputs: tuple[ReviewOutput, ...]
    steps: tuple
    section_output_token_limit: int
    consistency_output_token_limit: int


class ReviewSectionService[TSession: Session]:
    """Evidence-first 地顺序生成章节并形成可验证 ClaimSet。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        parse_revision_repo_factory: Callable[[TSession], ParseRevisionRepository],
        claim_set_repo_factory: Callable[[TSession], ClaimSetRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        model_gateway: ReviewSectionModelGateway,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._parse_revision_repo_factory = parse_revision_repo_factory
        self._claim_set_repo_factory = claim_set_repo_factory
        self._event_repo_factory = event_repo_factory
        self._model_gateway = model_gateway
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def draft_sections(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        approved_outline_output_id: str,
        evidence_matrix_output_id: str,
        correlation_id: str,
    ) -> SectionDraftingResult:
        context = await self._load_context(
            run_id, project_id, owner_id, approved_outline_output_id, evidence_matrix_output_id
        )
        await self._ensure_step(
            run_id,
            project_id,
            owner_id,
            ReviewStepKey.DRAFT_SECTIONS,
            10,
            {
                "outline_output_id": approved_outline_output_id,
                "evidence_matrix_output_id": evidence_matrix_output_id,
                "prompt_version": SECTION_PROMPT_VERSION,
                "schema_version": SECTION_SCHEMA_VERSION,
            },
        )
        existing_by_key = {
            item.output_key: item
            for item in context.outputs
            if item.output_type is ReviewOutputType.SECTION
        }
        outputs: list[ReviewOutput] = []
        summaries: list[dict[str, str]] = []
        terminology: dict[str, str] = {}
        invocations = 0
        for section in context.sections:
            rows = tuple(
                row for row in context.matrix_rows if row["dimension_key"] in section.dimension_keys
            )
            allowed_ids = {evidence_id for row in rows for evidence_id in row["evidence_ids"]}
            key = f"section:{section.section_key}"
            persisted = existing_by_key.get(key)
            idempotency_key = f"{run_id}:{key}:{SECTION_PROMPT_VERSION}"
            if persisted is None:
                prompt = self._section_messages(
                    context,
                    section,
                    rows,
                    allowed_ids,
                    summaries,
                    terminology,
                )
                result = await self._model_gateway.generate(
                    prompt,
                    json_schema=SECTION_JSON_SCHEMA,
                    max_tokens=context.section_output_token_limit,
                    run_id=run_id,
                )
                invocations += 1
                try:
                    draft = validate_section_draft(
                        parse_section_draft_json(result.content),
                        expected_section_key=section.section_key,
                        expected_title=section.title,
                        allowed_evidence_ids=allowed_ids,
                    )
                except SectionDraftValidationError as exc:
                    await self._fail_step(
                        run_id,
                        project_id,
                        owner_id,
                        ReviewStepKey.DRAFT_SECTIONS,
                        "section_draft_invalid",
                    )
                    raise ReviewSectionInvalidError("section_draft_invalid") from exc
                proposed = create_review_output(
                    review_run_id=run_id,
                    output_type=ReviewOutputType.SECTION,
                    output_key=key,
                    version=1,
                    schema_version=SECTION_SCHEMA_VERSION,
                    payload=draft.to_payload(),
                    idempotency_key=idempotency_key,
                )
                persisted = await self._persist_section_output(
                    proposed,
                    project_id=project_id,
                    owner_id=owner_id,
                    correlation_id=correlation_id,
                )
            draft = self._validate_persisted_section(
                persisted, section, allowed_ids, idempotency_key
            )
            outputs.append(persisted)
            summaries.append({"section_key": section.section_key, "summary": draft.summary})
            for item in draft.terminology:
                terminology.setdefault(item.term, item.definition)
        await self._complete_step_and_stage(
            run_id,
            project_id,
            owner_id,
            ReviewStepKey.DRAFT_SECTIONS,
            ReviewStage.VALIDATE_SECTIONS,
            {"section_output_ids": [item.output_id for item in outputs]},
        )
        return SectionDraftingResult(tuple(outputs), invocations)

    async def validate_sections(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        approved_outline_output_id: str,
        evidence_matrix_output_id: str,
        section_output_ids: list[str],
        correlation_id: str,
    ) -> CitationValidationOutcome:
        context = await self._load_context(
            run_id, project_id, owner_id, approved_outline_output_id, evidence_matrix_output_id
        )
        self._validate_prerequisite_step(
            context.steps,
            ReviewStepKey.DRAFT_SECTIONS,
            {"section_output_ids": list(section_output_ids)},
        )
        await self._ensure_step(
            run_id,
            project_id,
            owner_id,
            ReviewStepKey.VALIDATE_SECTIONS,
            11,
            {
                "outline_output_id": approved_outline_output_id,
                "evidence_matrix_output_id": evidence_matrix_output_id,
                "section_output_ids": list(section_output_ids),
                "validator_version": "citation-validator.v1",
                "schema_version": "claim-set.v1",
            },
        )
        try:
            drafts = self._ordered_section_drafts(context, section_output_ids)
            raw_claims = [claim for draft in drafts for claim in draft.claims]
            answer = RagAnswerOutput(
                answer_status=(
                    AnswerStatus.ANSWERED if raw_claims else AnswerStatus.INSUFFICIENT_EVIDENCE
                ),
                claims=[
                    ClaimDraft(text=claim.text, evidence_ids=list(claim.evidence_ids))
                    for claim in raw_claims
                ],
            )
            allowed = [
                context.evidence_by_id[evidence_id]
                for evidence_id in {
                    evidence_id
                    for row in context.matrix_rows
                    for evidence_id in row["evidence_ids"]
                }
            ]
            validation = validate_citations(answer, evidence=allowed, run_id=run_id)
            if not validation.passed:
                raise _CitationFailureError([item.reason for item in validation.failures])
            outcome = await self._persist_claim_set(
                run_id, project_id, owner_id, answer, correlation_id
            )
        except (
            ReviewSectionScopeError,
            SectionDraftValidationError,
            _CitationFailureError,
        ) as exc:
            reasons = (
                exc.reasons
                if isinstance(exc, _CitationFailureError)
                else [CitationFailureReason.FABRICATED_EVIDENCE]
            )
            await self._record_citation_result(
                run_id, project_id, owner_id, correlation_id, False, reasons
            )
            raise ReviewCitationInvalidError("citation_validation_failed") from exc
        return outcome

    async def consistency_check(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        approved_outline_output_id: str,
        evidence_matrix_output_id: str,
        section_output_ids: list[str],
        claim_set_id: str,
    ) -> ReviewOutput:
        context = await self._load_context(
            run_id, project_id, owner_id, approved_outline_output_id, evidence_matrix_output_id
        )
        self._validate_claim_set_step(context, section_output_ids, claim_set_id)
        await self._ensure_step(
            run_id,
            project_id,
            owner_id,
            ReviewStepKey.CONSISTENCY_CHECK,
            12,
            {
                "outline_output_id": approved_outline_output_id,
                "evidence_matrix_output_id": evidence_matrix_output_id,
                "section_output_ids": list(section_output_ids),
                "claim_set_id": claim_set_id,
                "prompt_version": CONSISTENCY_PROMPT_VERSION,
                "schema_version": CONSISTENCY_SCHEMA_VERSION,
            },
        )
        drafts = self._ordered_section_drafts(context, section_output_ids)
        existing = next(
            (
                item
                for item in context.outputs
                if item.output_type is ReviewOutputType.CONSISTENCY_REPORT
                and item.output_key == "consistency-report"
            ),
            None,
        )
        key = f"{run_id}:consistency-report:{CONSISTENCY_PROMPT_VERSION}"
        if existing is None:
            result = await self._model_gateway.generate(
                self._consistency_messages(drafts),
                json_schema=CONSISTENCY_JSON_SCHEMA,
                max_tokens=context.consistency_output_token_limit,
                run_id=run_id,
            )
            try:
                report = validate_consistency_report(
                    parse_consistency_report_json(result.content),
                    allowed_section_keys=tuple(item.section_key for item in drafts),
                )
            except ConsistencyReportValidationError as exc:
                await self._fail_step(
                    run_id,
                    project_id,
                    owner_id,
                    ReviewStepKey.CONSISTENCY_CHECK,
                    "consistency_report_invalid",
                )
                raise ReviewSectionInvalidError("consistency_report_invalid") from exc
            proposed = create_review_output(
                review_run_id=run_id,
                output_type=ReviewOutputType.CONSISTENCY_REPORT,
                output_key="consistency-report",
                version=1,
                schema_version=CONSISTENCY_SCHEMA_VERSION,
                payload=report.to_payload(),
                idempotency_key=key,
            )
            existing = await self._persist_output(proposed, project_id, owner_id)
        self._validate_consistency_output(existing, drafts, key)
        await self._complete_step_and_stage(
            run_id,
            project_id,
            owner_id,
            ReviewStepKey.CONSISTENCY_CHECK,
            ReviewStage.EXPORT_REVIEW,
            {"consistency_output_id": existing.output_id},
        )
        return existing

    async def _load_context(self, run_id, project_id, owner_id, outline_id, matrix_id):
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped(run_id, project_id, owner_id)
            if (
                run is None
                or review is None
                or run.owner_id != owner_id
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
            ):
                raise RunNotFoundError(run_id)
            if (
                review.workflow_version != WORKFLOW_VERSION
                or review.model_profile_version not in SUPPORTED_MODEL_PROFILE_VERSIONS
                or review.prompt_versions.get("section_draft") != SECTION_PROMPT_VERSION
                or review.prompt_versions.get("consistency_check") != CONSISTENCY_PROMPT_VERSION
                or review.current_outline_output_id != outline_id
            ):
                raise ReviewSectionScopeError("Review 版本或批准大纲范围非法")
            outputs = tuple(await repo.list_outputs_scoped(run_id, project_id, owner_id))
            steps = tuple(await repo.list_steps_scoped(run_id, project_id, owner_id))
            self._validate_prerequisite_step(
                steps,
                ReviewStepKey.BUILD_EVIDENCE_MATRIX,
                {"evidence_matrix_output_id": matrix_id},
            )
            self._validate_prerequisite_step(
                steps,
                ReviewStepKey.REVIEW_OUTLINE,
                {"outline_output_id": outline_id},
            )
            outline = self._find_output(outputs, outline_id, ReviewOutputType.OUTLINE, "outline.v1")
            matrix = self._find_output(
                outputs, matrix_id, ReviewOutputType.EVIDENCE_MATRIX, "evidence-matrix.v1"
            )
            outline_value = validate_outline(
                outline.payload,
                allowed_dimension_keys={
                    str(row["dimension_key"])
                    for row in matrix.payload.get("rows", [])
                    if isinstance(row, dict) and isinstance(row.get("dimension_key"), str)
                },
            )
            sections = tuple(
                _SectionSpec(item.section_key, item.title, item.purpose, item.dimension_keys)
                for item in outline_value.sections
            )
            rows = self._normalize_matrix_rows(matrix.payload)
            sources = [
                item
                for item in await repo.list_sources_scoped(run_id, project_id, owner_id)
                if item.status is ReviewSourceStatus.READY
                and item.paper_id
                and item.paper_version_id
            ]
            source_by_paper = {item.paper_id or "": item for item in sources}
            if not source_by_paper or len(source_by_paper) != len(sources):
                raise ReviewSectionScopeError("Review Source Paper 范围非法")
            ids = sorted({eid for row in rows for eid in row["evidence_ids"]})
            evidence = await self._evidence_repo_factory(session).list_by_ids(ids)
            evidence_by_id = {item.evidence_id: item for item in evidence}
            if set(evidence_by_id) != set(ids):
                raise ReviewSectionScopeError("Matrix Evidence 不完整")
            version_repo = self._paper_version_repo_factory(session)
            revision_repo = self._parse_revision_repo_factory(session)
            for row in rows:
                source = source_by_paper.get(row["paper_id"])
                if source is None:
                    raise ReviewSectionScopeError("Matrix Paper 不属于 READY Source")
                version = await version_repo.get_by_id(source.paper_version_id or "")
                if (
                    version is None
                    or version.owner_id != owner_id
                    or version.paper_id != row["paper_id"]
                ):
                    raise ReviewSectionScopeError("Matrix PaperVersion 归属非法")
                for evidence_id in row["evidence_ids"]:
                    item = evidence_by_id[evidence_id]
                    revision = await revision_repo.get_by_id(item.parse_revision_id)
                    if (
                        item.run_id != run_id
                        or item.project_id != project_id
                        or item.paper_id != row["paper_id"]
                        or item.version_id != source.paper_version_id
                        or revision is None
                        or revision.version_id != item.version_id
                        or revision.status is not ParseRevisionStatus.SUCCEEDED
                        or version.current_parse_revision_id != item.parse_revision_id
                    ):
                        raise ReviewSectionScopeError("Matrix 行的 Evidence 闭包非法")
            return _ReviewContext(
                review.research_question,
                outline_id,
                matrix_id,
                sections,
                rows,
                evidence_by_id,
                source_by_paper,
                outputs,
                steps,
                self._profile_token_limit(
                    review.config_snapshot,
                    "section_output_token_limit",
                    SECTION_MAX_TOKENS,
                    _MAX_SECTION_OUTPUT_TOKENS,
                ),
                self._profile_token_limit(
                    review.config_snapshot,
                    "consistency_output_token_limit",
                    CONSISTENCY_MAX_TOKENS,
                    _MAX_CONSISTENCY_OUTPUT_TOKENS,
                ),
            )

    @staticmethod
    def _profile_token_limit(snapshot, key, fallback, maximum):
        value = snapshot.get(key, fallback)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ReviewSectionScopeError(f"Review Profile {key} 非法")
        if not _MIN_OUTPUT_TOKENS <= value <= maximum:
            raise ReviewSectionScopeError(f"Review Profile {key} 超出范围")
        return value

    @staticmethod
    def _validate_prerequisite_step(steps, key, output_refs):
        step = next((item for item in steps if item.step_key is key), None)
        if (
            step is None
            or step.status is not ReviewStepStatus.SUCCEEDED
            or step.output_refs != output_refs
        ):
            raise ReviewSectionScopeError(f"{key.value} Step 闭包非法")

    @staticmethod
    def _validate_claim_set_step(context, section_output_ids, claim_set_id):
        ReviewSectionService._validate_prerequisite_step(
            context.steps,
            ReviewStepKey.VALIDATE_SECTIONS,
            {"claim_set_id": claim_set_id},
        )
        step = next(
            item
            for item in context.steps
            if item.step_key is ReviewStepKey.VALIDATE_SECTIONS
        )
        expected_input_refs = {
            "outline_output_id": context.outline_id,
            "evidence_matrix_output_id": context.matrix_id,
            "section_output_ids": list(section_output_ids),
            "validator_version": "citation-validator.v1",
            "schema_version": "claim-set.v1",
        }
        if step.input_refs != expected_input_refs:
            raise ReviewSectionScopeError("validate_sections Step 输入闭包非法")

    @staticmethod
    def _normalize_matrix_rows(payload: dict) -> tuple[dict, ...]:
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ReviewSectionScopeError("Matrix rows 非法")
        normalized: list[dict] = []
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row)
                != {
                    "paper_id",
                    "dimension_key",
                    "status",
                    "finding",
                    "limitations",
                    "evidence_ids",
                }
                or not isinstance(row.get("paper_id"), str)
                or not isinstance(row.get("dimension_key"), str)
                or not isinstance(row.get("evidence_ids"), list)
                or any(not isinstance(item, str) for item in row["evidence_ids"])
                or len(set(row["evidence_ids"])) != len(row["evidence_ids"])
            ):
                raise ReviewSectionScopeError("Matrix 行结构非法")
            if row.get("status") == "extracted":
                if not isinstance(row.get("finding"), str) or not row["evidence_ids"]:
                    raise ReviewSectionScopeError("extracted Matrix 行非法")
            elif row.get("status") == "insufficient_evidence":
                if (
                    row.get("finding") is not None
                    or row.get("limitations") is not None
                    or row["evidence_ids"]
                ):
                    raise ReviewSectionScopeError("证据不足 Matrix 行非法")
            else:
                raise ReviewSectionScopeError("Matrix 状态非法")
            normalized.append(dict(row))
        return tuple(normalized)

    @staticmethod
    def _find_output(outputs, output_id, output_type, schema_version):
        found = next(
            (
                item
                for item in outputs
                if item.output_id == output_id
                and item.output_type is output_type
                and item.schema_version == schema_version
            ),
            None,
        )
        if found is None:
            raise ReviewSectionScopeError("Review Output 不属于当前范围或版本不受支持")
        return found

    @staticmethod
    def _section_messages(context, section, rows, allowed_ids, summaries, terminology):
        evidence = [context.evidence_by_id[item] for item in sorted(allowed_ids)]
        payload = {
            "prompt_version": SECTION_PROMPT_VERSION,
            "research_question": context.question,
            "section": {
                "section_key": section.section_key,
                "title": section.title,
                "purpose": section.purpose,
                "dimension_keys": list(section.dimension_keys),
            },
            "matrix_rows": list(rows),
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "paper_id": item.paper_id,
                    "version_id": item.version_id,
                    "parse_revision_id": item.parse_revision_id,
                    "section_path": item.section_path,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                    "text": item.excerpt,
                }
                for item in evidence
            ],
            "prior_section_summaries": list(summaries),
            "terminology": dict(terminology),
            "citation_rules": (
                "每个重要 Claim 必须绑定本输入 evidence_id；证据不足时不得生成 Claim。"
            ),
            "token_budget": context.section_output_token_limit,
            "output_schema": SECTION_SCHEMA_VERSION,
        }
        return [
            ChatMessage(role="system", content="按受控证据生成一个综述章节的结构化 Claim。"),
            ChatMessage(
                role="user", content=json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        ]

    @staticmethod
    def _consistency_messages(drafts: list[SectionDraft]):
        payload = {
            "prompt_version": CONSISTENCY_PROMPT_VERSION,
            "sections": [
                {
                    "section_key": item.section_key,
                    "summary": item.summary,
                    "claims": [claim.text for claim in item.claims],
                    "terminology": [
                        {"term": term.term, "definition": term.definition}
                        for term in item.terminology
                    ],
                }
                for item in drafts
            ],
            "output_schema": CONSISTENCY_SCHEMA_VERSION,
        }
        return [
            ChatMessage(role="system", content="只报告章节间术语、矛盾和冗余问题，不重写章节。"),
            ChatMessage(
                role="user", content=json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        ]

    def _validate_persisted_section(self, output, section, allowed_ids, key):
        if (
            output.output_type is not ReviewOutputType.SECTION
            or output.output_key != f"section:{section.section_key}"
            or output.version != 1
            or output.schema_version != SECTION_SCHEMA_VERSION
            or output.idempotency_key != key
        ):
            raise IdempotencyConflictError(key)
        try:
            parsed = parse_section_draft_json(json.dumps(output.payload, ensure_ascii=False))
            return validate_section_draft(
                parsed,
                expected_section_key=section.section_key,
                expected_title=section.title,
                allowed_evidence_ids=allowed_ids,
            )
        except SectionDraftValidationError as exc:
            raise ReviewSectionScopeError("既有 Section Output 闭包非法") from exc

    def _ordered_section_drafts(self, context, ids):
        if len(ids) != len(context.sections) or len(set(ids)) != len(ids):
            raise ReviewSectionScopeError("Section Output ID 集合与批准大纲不一致")
        by_id = {item.output_id: item for item in context.outputs}
        drafts: list[SectionDraft] = []
        for section, output_id in zip(context.sections, ids, strict=True):
            output = by_id.get(output_id)
            if output is None:
                raise ReviewSectionScopeError("Section Output 不属于当前 Review Run")
            allowed_ids = {
                evidence_id
                for row in context.matrix_rows
                if row["dimension_key"] in section.dimension_keys
                for evidence_id in row["evidence_ids"]
            }
            drafts.append(
                self._validate_persisted_section(
                    output,
                    section,
                    allowed_ids,
                    f"{output.review_run_id}:section:{section.section_key}:{SECTION_PROMPT_VERSION}",
                )
            )
        return drafts

    async def _persist_section_output(self, output, *, project_id, owner_id, correlation_id):
        emitted = False
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(output.review_run_id, owner_id)
            repo = self._review_repo_factory(session)
            if (
                run is None
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
            ):
                raise RunNotFoundError(output.review_run_id)
            before = await repo.list_outputs_scoped(output.review_run_id, project_id, owner_id)
            persisted = await repo.get_or_add_output(output)
            if (
                persisted.review_run_id != output.review_run_id
                or persisted.output_type is not output.output_type
                or persisted.output_key != output.output_key
                or persisted.version != output.version
                or persisted.schema_version != output.schema_version
                or persisted.payload != output.payload
                or persisted.idempotency_key != output.idempotency_key
            ):
                raise IdempotencyConflictError(output.idempotency_key)
            if not any(item.output_id == persisted.output_id for item in before):
                if not await run_repo.update_status(
                    run.run_id, run.status, run.status, run.event_sequence + 1
                ):
                    raise RunConcurrentModificationError(run.run_id)
                await self._event_repo_factory(session).add(
                    create_event(
                        run.run_id,
                        run.event_sequence,
                        "section_draft_completed",
                        "system",
                        correlation_id,
                        {
                            "section_key": persisted.payload["section_key"],
                            "output_id": persisted.output_id,
                        },
                    )
                )
                emitted = True
            await session.commit()
        if emitted:
            await notify_run_event(self._event_notifier, output.review_run_id)
        return persisted

    async def _persist_output(self, output, project_id, owner_id):
        async with self._session_factory() as session:
            await self._lock_active_run(
                session, output.review_run_id, project_id, owner_id
            )
            repo = self._review_repo_factory(session)
            if await repo.get_review_run_scoped(output.review_run_id, project_id, owner_id) is None:
                raise RunNotFoundError(output.review_run_id)
            persisted = await repo.get_or_add_output(output)
            if (
                persisted.review_run_id != output.review_run_id
                or persisted.output_type is not output.output_type
                or persisted.output_key != output.output_key
                or persisted.version != output.version
                or persisted.schema_version != output.schema_version
                or persisted.payload != output.payload
                or persisted.idempotency_key != output.idempotency_key
            ):
                raise IdempotencyConflictError(output.idempotency_key)
            await session.commit()
            return persisted

    async def _persist_claim_set(self, run_id, project_id, owner_id, answer, correlation_id):
        emitted = False
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(run_id, owner_id)
            review_repo = self._review_repo_factory(session)
            if (
                run is None
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
            ):
                raise RunNotFoundError(run_id)
            repo = self._claim_set_repo_factory(session)
            proposal = create_claim_set(run_id, answer.answer_status)
            claim_set = await repo.get_or_add_claim_set(proposal)
            if claim_set.answer_status is not answer.answer_status:
                raise IdempotencyConflictError(f"{run_id}:claim-set")
            proposed_claims = [
                create_claim(claim_set.claim_set_id, sequence, item.text)
                for sequence, item in enumerate(answer.claims, 1)
            ]
            claims = await repo.get_or_add_claims(proposed_claims)
            all_claims = await repo.list_claims(claim_set.claim_set_id)
            if [(x.sequence, x.text) for x in all_claims] != [
                (x.sequence, x.text) for x in proposed_claims
            ]:
                raise IdempotencyConflictError(f"{run_id}:claims")
            claims = all_claims
            citations = [
                Citation(claim.claim_id, evidence_id)
                for claim, raw in zip(claims, answer.claims, strict=True)
                for evidence_id in raw.evidence_ids
            ]
            persisted_citations = await repo.get_or_add_citations(citations)
            all_citations = [
                item
                for claim in claims
                for item in await repo.list_citations(claim.claim_id)
            ]
            if {(x.claim_id, x.evidence_id) for x in persisted_citations} != {
                (x.claim_id, x.evidence_id) for x in citations
            } or {(x.claim_id, x.evidence_id) for x in all_citations} != {
                (x.claim_id, x.evidence_id) for x in citations
            }:
                raise IdempotencyConflictError(f"{run_id}:citations")
            step = await self._get_step(
                review_repo, run_id, project_id, owner_id, ReviewStepKey.VALIDATE_SECTIONS
            )
            refs = {"claim_set_id": claim_set.claim_set_id}
            if step.status is ReviewStepStatus.RUNNING:
                if not await review_repo.advance_step(
                    step.succeed(refs), ReviewStepStatus.RUNNING.value
                ):
                    raise RunConcurrentModificationError(run_id)
            elif step.status is not ReviewStepStatus.SUCCEEDED or step.output_refs != refs:
                raise IdempotencyConflictError(step.idempotency_key)
            review = await review_repo.get_review_run_scoped_for_update(
                run_id, project_id, owner_id
            )
            if review is None:
                raise RunNotFoundError(run_id)
            if review.current_stage is ReviewStage.VALIDATE_SECTIONS:
                updated = replace(
                    review,
                    current_stage=ReviewStage.CONSISTENCY_CHECK,
                    updated_at=datetime.now(UTC),
                )
                if not await review_repo.advance_review_stage(
                    updated, expected_stage=ReviewStage.VALIDATE_SECTIONS.value
                ):
                    raise RunConcurrentModificationError(run_id)
            existing_events = await self._event_repo_factory(session).list_by_run(run_id)
            if not any(
                item.event_type == "citation_validation_completed" for item in existing_events
            ):
                if not await run_repo.update_status(
                    run_id, run.status, run.status, run.event_sequence + 1
                ):
                    raise RunConcurrentModificationError(run_id)
                await self._event_repo_factory(session).add(
                    create_event(
                        run_id,
                        run.event_sequence,
                        "citation_validation_completed",
                        "system",
                        correlation_id,
                        {
                            "passed": True,
                            "claim_set_id": claim_set.claim_set_id,
                            "claim_count": len(claims),
                        },
                    )
                )
                emitted = True
            await session.commit()
            outcome = CitationValidationOutcome(claim_set, tuple(claims), tuple(citations))
        if emitted:
            await notify_run_event(self._event_notifier, run_id)
        return outcome

    async def _record_citation_result(
        self, run_id, project_id, owner_id, correlation_id, passed, reasons
    ):
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(run_id, owner_id)
            repo = self._review_repo_factory(session)
            if (
                run is None
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
            ):
                raise RunNotFoundError(run_id)
            step = await self._get_step(
                repo, run_id, project_id, owner_id, ReviewStepKey.VALIDATE_SECTIONS
            )
            if step.status in {
                ReviewStepStatus.PENDING,
                ReviewStepStatus.RUNNING,
            } and not await repo.advance_step(
                step.fail("citation_validation_failed"), step.status.value
            ):
                raise RunConcurrentModificationError(run_id)
            events = await self._event_repo_factory(session).list_by_run(run_id)
            if not any(item.event_type == "citation_validation_completed" for item in events):
                if not await run_repo.update_status(
                    run_id, run.status, run.status, run.event_sequence + 1
                ):
                    raise RunConcurrentModificationError(run_id)
                counts = Counter(item.value for item in reasons)
                await self._event_repo_factory(session).add(
                    create_event(
                        run_id,
                        run.event_sequence,
                        "citation_validation_completed",
                        "system",
                        correlation_id,
                        {"passed": passed, "failure_reasons": dict(sorted(counts.items()))},
                    )
                )
            await session.commit()
        await notify_run_event(self._event_notifier, run_id)

    async def _ensure_step(self, run_id, project_id, owner_id, key, sequence, input_refs):
        async with self._session_factory() as session:
            await self._lock_active_run(session, run_id, project_id, owner_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped_for_update(
                run_id, project_id, owner_id
            )
            if review is None:
                raise RunNotFoundError(run_id)
            proposed = create_run_step(
                run_id=run_id,
                step_key=key,
                sequence=sequence,
                idempotency_key=f"{run_id}:{key.value}",
                input_refs=input_refs,
            )
            step = await repo.get_or_add_step(proposed)
            if (
                step.run_id != proposed.run_id
                or step.step_key is not proposed.step_key
                or step.sequence != proposed.sequence
                or step.idempotency_key != proposed.idempotency_key
                or step.input_refs != proposed.input_refs
            ):
                raise IdempotencyConflictError(proposed.idempotency_key)
            if step.status is ReviewStepStatus.PENDING:
                running = step.start()
                if not await repo.advance_step(running, ReviewStepStatus.PENDING.value):
                    raise RunConcurrentModificationError(run_id)
            elif step.status not in {ReviewStepStatus.RUNNING, ReviewStepStatus.SUCCEEDED}:
                raise ReviewSectionScopeError(f"{key.value} Step 不可恢复")
            stage_by_key = {
                ReviewStepKey.DRAFT_SECTIONS: ReviewStage.DRAFT_SECTIONS,
                ReviewStepKey.VALIDATE_SECTIONS: ReviewStage.VALIDATE_SECTIONS,
                ReviewStepKey.CONSISTENCY_CHECK: ReviewStage.CONSISTENCY_CHECK,
            }
            target_stage = stage_by_key[key]
            if _STAGE_ORDER[review.current_stage] < _STAGE_ORDER[target_stage]:
                updated = replace(
                    review,
                    current_stage=target_stage,
                    updated_at=datetime.now(UTC),
                )
                if not await repo.advance_review_stage(
                    updated, expected_stage=review.current_stage.value
                ):
                    raise RunConcurrentModificationError(run_id)
            await session.commit()

    async def _complete_step_and_stage(self, run_id, project_id, owner_id, key, next_stage, refs):
        async with self._session_factory() as session:
            await self._lock_active_run(session, run_id, project_id, owner_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped_for_update(run_id, project_id, owner_id)
            if review is None:
                raise RunNotFoundError(run_id)
            step = await self._get_step(repo, run_id, project_id, owner_id, key)
            if step.status is ReviewStepStatus.RUNNING:
                if not await repo.advance_step(step.succeed(refs), ReviewStepStatus.RUNNING.value):
                    raise RunConcurrentModificationError(run_id)
            elif step.status is not ReviewStepStatus.SUCCEEDED or step.output_refs != refs:
                raise IdempotencyConflictError(step.idempotency_key)
            if _STAGE_ORDER[review.current_stage] < _STAGE_ORDER[next_stage]:
                updated = replace(review, current_stage=next_stage, updated_at=datetime.now(UTC))
                if not await repo.advance_review_stage(
                    updated, expected_stage=review.current_stage.value
                ):
                    raise RunConcurrentModificationError(run_id)
            await session.commit()

    async def _fail_step(self, run_id, project_id, owner_id, key, code):
        async with self._session_factory() as session:
            await self._lock_active_run(session, run_id, project_id, owner_id)
            repo = self._review_repo_factory(session)
            step = await self._get_step(repo, run_id, project_id, owner_id, key)
            if step.status in {
                ReviewStepStatus.PENDING,
                ReviewStepStatus.RUNNING,
            } and not await repo.advance_step(step.fail(code), step.status.value):
                raise RunConcurrentModificationError(run_id)
            await session.commit()

    async def _lock_active_run(self, session, run_id, project_id, owner_id):
        run = await self._run_repo_factory(session).get_by_id_for_update(run_id, owner_id)
        if (
            run is None
            or run.project_id != project_id
            or run.run_type != RunType.REVIEW.value
            or run.status is not RunStatus.RUNNING
        ):
            raise RunNotFoundError(run_id)
        return run

    @staticmethod
    async def _get_step(repo, run_id, project_id, owner_id, key):
        step = next(
            (
                item
                for item in await repo.list_steps_scoped(run_id, project_id, owner_id)
                if item.step_key is key
            ),
            None,
        )
        if step is None:
            raise ReviewSectionScopeError(f"{key.value} Step 不存在")
        return step

    @staticmethod
    def _validate_consistency_output(output, drafts, key):
        if (
            output.output_type is not ReviewOutputType.CONSISTENCY_REPORT
            or output.output_key != "consistency-report"
            or output.version != 1
            or output.schema_version != CONSISTENCY_SCHEMA_VERSION
            or output.idempotency_key != key
        ):
            raise IdempotencyConflictError(key)
        try:
            validate_consistency_report(
                parse_consistency_report_json(json.dumps(output.payload, ensure_ascii=False)),
                allowed_section_keys=tuple(item.section_key for item in drafts),
            )
        except ConsistencyReportValidationError as exc:
            raise ReviewSectionScopeError("既有 Consistency Output 非法") from exc


class _CitationFailureError(Exception):
    def __init__(self, reasons: list[CitationFailureReason]) -> None:
        self.reasons = reasons
        super().__init__("citation_validation_failed")
