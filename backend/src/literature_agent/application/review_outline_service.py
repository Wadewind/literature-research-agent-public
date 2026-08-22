"""大纲生成、人工请求、提交与持久化恢复用例。"""

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.waiting_run_resume_service import (
    ResumeReason,
    WaitingRunResumeService,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    HumanInputConflictError,
    IdempotencyConflictError,
    ReviewOutlineInvalidError,
    ReviewOutlineScopeError,
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.model_types import ChatMessage, ChatResult
from literature_agent.domain.review import (
    HumanInput,
    HumanInputAction,
    HumanInputRequest,
    HumanInputRequestStatus,
    ReviewOutput,
    ReviewOutputType,
    ReviewSourceStatus,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    create_human_input,
    create_human_input_request,
    create_review_output,
    create_run_step,
)
from literature_agent.domain.review_evidence_matrix import (
    AnalysisDimension,
    EvidenceMatrixValidationError,
    validate_evidence_matrix,
)
from literature_agent.domain.review_outline import (
    OutlineValidationError,
    parse_outline_json,
    validate_feedback,
    validate_outline,
)
from literature_agent.domain.run import RunStatus, RunType

PROMPT_VERSION = "outline_generate.v1"
OUTLINE_SCHEMA_VERSION = "outline.v1"
SEARCH_STRATEGY_SCHEMA_VERSION = "search-strategy.v1"
EVIDENCE_MATRIX_SCHEMA_VERSION = "evidence-matrix.v1"
WORKFLOW_VERSION = "review.v1"
MODEL_PROFILE_VERSION = "review-default.v1"

OUTLINE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sections"],
    "properties": {
        "sections": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["section_key", "title", "purpose", "dimension_keys"],
                "properties": {
                    "section_key": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9_]{0,63}$",
                    },
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "purpose": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "dimension_keys": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 6,
                        "uniqueItems": True,
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}


class OutlineModelGateway(Protocol):
    """大纲结构化生成使用的最小模型边界。"""

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
    ) -> ChatResult: ...


@dataclass(frozen=True, slots=True)
class OutlineProposalResult:
    output: ReviewOutput
    request: HumanInputRequest
    model_invocations: int


@dataclass(frozen=True, slots=True)
class HumanInputSubmitResult:
    human_input: HumanInput
    approved_outline_output_id: str | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class PersistedOutlineDecision:
    action: HumanInputAction
    human_input_id: str
    request_id: str
    current_outline_output_id: str
    approved_outline_output_id: str | None
    feedback: str | None


class ReviewOutlineService[TSession: Session]:
    """从受控 Matrix 生成版本化大纲，并原子进入 WAITING_INPUT。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        model_gateway: OutlineModelGateway,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._event_repo_factory = event_repo_factory
        self._model_gateway = model_gateway
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def propose_and_pause(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        search_strategy_output_id: str,
        evidence_matrix_output_id: str,
        feedback_round: int,
        correlation_id: str,
        feedback_human_input_id: str | None = None,
    ) -> OutlineProposalResult:
        if feedback_round < 0:
            raise ReviewOutlineScopeError("feedback_round 不能为负数")
        context = await self._load_context(
            run_id=run_id,
            project_id=project_id,
            owner_id=owner_id,
            search_strategy_output_id=search_strategy_output_id,
            evidence_matrix_output_id=evidence_matrix_output_id,
            feedback_round=feedback_round,
            feedback_human_input_id=feedback_human_input_id,
        )
        version = feedback_round + 1
        idempotency_key = f"{run_id}:outline:{version}:{PROMPT_VERSION}"
        existing = next(
            (item for item in context["outputs"] if item.idempotency_key == idempotency_key),
            None,
        )
        model_invocations = 0
        if existing is None:
            result = await self._model_gateway.generate(
                self._messages(context),
                json_schema=OUTLINE_JSON_SCHEMA,
                max_tokens=4_000,
                run_id=run_id,
            )
            model_invocations = 1
            try:
                outline = validate_outline(
                    parse_outline_json(result.content).to_payload(),
                    allowed_dimension_keys=context["dimension_keys"],
                )
            except OutlineValidationError as exc:
                raise ReviewOutlineInvalidError("outline_invalid") from exc
            proposed = create_review_output(
                review_run_id=run_id,
                output_type=ReviewOutputType.OUTLINE,
                output_key="outline",
                version=version,
                schema_version=OUTLINE_SCHEMA_VERSION,
                payload=outline.to_payload(),
                idempotency_key=idempotency_key,
            )
            output = await self._persist_output(
                proposed,
                project_id=project_id,
                owner_id=owner_id,
                dimension_keys=context["dimension_keys"],
            )
        else:
            output = self._validate_outline_output(
                existing,
                run_id=run_id,
                version=version,
                idempotency_key=idempotency_key,
                dimension_keys=context["dimension_keys"],
            )
        request = await self._persist_request_and_pause(
            output=output,
            project_id=project_id,
            owner_id=owner_id,
            correlation_id=correlation_id,
            expected_previous_outline_id=context["previous_outline_output_id"],
        )
        return OutlineProposalResult(output, request, model_invocations)

    async def _load_context(self, **kwargs) -> dict:
        run_id = kwargs["run_id"]
        project_id = kwargs["project_id"]
        owner_id = kwargs["owner_id"]
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
                or review.model_profile_version != MODEL_PROFILE_VERSION
                or review.prompt_versions.get("outline_generate") != PROMPT_VERSION
            ):
                raise ReviewOutlineScopeError("Review Outline 版本快照不受支持")
            outputs = await repo.list_outputs_scoped(run_id, project_id, owner_id)
            strategy = self._find_output(
                outputs,
                kwargs["search_strategy_output_id"],
                ReviewOutputType.SEARCH_STRATEGY,
                SEARCH_STRATEGY_SCHEMA_VERSION,
            )
            matrix = self._find_output(
                outputs,
                kwargs["evidence_matrix_output_id"],
                ReviewOutputType.EVIDENCE_MATRIX,
                EVIDENCE_MATRIX_SCHEMA_VERSION,
            )
            if matrix.output_key != "evidence-matrix" or matrix.version != 1:
                raise ReviewOutlineScopeError("Evidence Matrix 聚合 Output 身份非法")
            dimensions = strategy.payload.get("dimensions")
            if not isinstance(dimensions, list) or not 3 <= len(dimensions) <= 6:
                raise ReviewOutlineScopeError("Search Strategy 维度非法")
            normalized_dimensions: list[dict[str, str]] = []
            analysis_dimensions: list[AnalysisDimension] = []
            for raw in dimensions:
                if not isinstance(raw, dict) or set(raw) != {
                    "dimension_key",
                    "name",
                    "extraction_question",
                }:
                    raise ReviewOutlineScopeError("Search Strategy 维度非法")
                if not all(isinstance(raw[key], str) and raw[key] for key in raw):
                    raise ReviewOutlineScopeError("Search Strategy 维度非法")
                normalized_dimensions.append(dict(raw))
                try:
                    analysis_dimensions.append(
                        AnalysisDimension(
                            raw["dimension_key"], raw["name"], raw["extraction_question"]
                        )
                    )
                except ValueError as exc:
                    raise ReviewOutlineScopeError("Search Strategy 维度非法") from exc
            dimension_keys = tuple(item["dimension_key"] for item in normalized_dimensions)
            if len(set(dimension_keys)) != len(dimension_keys):
                raise ReviewOutlineScopeError("Search Strategy 维度重复")
            sources = await repo.list_sources_scoped(run_id, project_id, owner_id)
            scoped_sources = [
                item
                for item in sources
                if item.status is ReviewSourceStatus.READY
                and item.paper_id
                and item.paper_version_id
            ]
            if not scoped_sources:
                raise ReviewOutlineScopeError("Review Run 没有 ready 来源")
            source_by_paper = {item.paper_id: item for item in scoped_sources}
            if len(source_by_paper) != len(scoped_sources):
                raise ReviewOutlineScopeError("Review Source Paper 重复")
            evidence_ids = self._matrix_evidence_ids(matrix.payload)
            evidence = await self._evidence_repo_factory(session).list_by_ids(
                sorted(set(evidence_ids))
            )
            evidence_by_id = {item.evidence_id: item for item in evidence}
            if set(evidence_by_id) != set(evidence_ids) or any(
                item.run_id != run_id or item.project_id != project_id for item in evidence
            ):
                raise ReviewOutlineScopeError("Evidence Matrix 引用闭包非法")
            summary = self._validate_matrix(
                matrix.payload,
                dimensions=tuple(analysis_dimensions),
                source_by_paper=source_by_paper,
                evidence_by_id=evidence_by_id,
                run_id=run_id,
                project_id=project_id,
            )
            feedback = None
            feedback_id = kwargs["feedback_human_input_id"]
            round_number = kwargs["feedback_round"]
            previous_outline_output_id = review.current_outline_output_id
            if round_number == 0:
                if feedback_id is not None or previous_outline_output_id is not None:
                    raise ReviewOutlineScopeError("初始大纲状态非法")
            else:
                if feedback_id is None or previous_outline_output_id is None:
                    raise ReviewOutlineScopeError("反馈轮缺少持久 HumanInput 或前一版大纲")
                human_input = await repo.get_human_input_scoped(
                    feedback_id, run_id, project_id, owner_id
                )
                if human_input is None or human_input.action is not HumanInputAction.FEEDBACK:
                    raise ReviewOutlineScopeError("反馈 HumanInput 不属于当前 Review Run")
                request = await repo.get_human_input_request_scoped_for_update(
                    human_input.request_id, run_id, project_id, owner_id
                )
                if (
                    request is None
                    or request.status is not HumanInputRequestStatus.RESOLVED
                    or request.resolved_input_id != human_input.human_input_id
                    or request.outline_output_id != previous_outline_output_id
                ):
                    raise ReviewOutlineScopeError("反馈 HumanInput 与当前 Outline Request 不闭合")
                feedback = validate_feedback(human_input.payload.get("feedback"))
                if human_input.request_version != round_number:
                    raise ReviewOutlineScopeError("feedback_round 与 Request 版本不匹配")
            return {
                "run_id": run_id,
                "question": review.research_question,
                "dimensions": normalized_dimensions,
                "dimension_keys": dimension_keys,
                "matrix_summary": summary,
                "coverage": matrix.payload.get("summary", {}),
                "feedback": feedback,
                "feedback_round": round_number,
                "outputs": outputs,
                "previous_outline_output_id": previous_outline_output_id,
            }

    @staticmethod
    def _find_output(outputs, output_id, output_type, schema_version) -> ReviewOutput:
        output = next(
            (
                item
                for item in outputs
                if item.output_id == output_id
                and item.output_type is output_type
                and item.schema_version == schema_version
            ),
            None,
        )
        if output is None:
            raise ReviewOutlineScopeError("大纲输入 Output 不属于当前 Review Run")
        return output

    @staticmethod
    def _matrix_evidence_ids(payload: dict) -> list[str]:
        if not isinstance(payload, dict) or set(payload) != {"rows", "paper_failures", "summary"}:
            raise ReviewOutlineScopeError("Evidence Matrix 聚合结构非法")
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ReviewOutlineScopeError("Evidence Matrix 没有有效行")
        evidence_ids: list[str] = []
        for raw in rows:
            required = {
                "paper_id",
                "dimension_key",
                "status",
                "finding",
                "limitations",
                "evidence_ids",
            }
            if (
                not isinstance(raw, dict)
                or set(raw) != required
                or not isinstance(raw["evidence_ids"], list)
                or any(not isinstance(item, str) for item in raw["evidence_ids"])
                or len(set(raw["evidence_ids"])) != len(raw["evidence_ids"])
            ):
                raise ReviewOutlineScopeError("Evidence Matrix 行结构非法")
            evidence_ids.extend(raw["evidence_ids"])
        return evidence_ids

    @staticmethod
    def _validate_matrix(
        payload: dict,
        *,
        dimensions: tuple[AnalysisDimension, ...],
        source_by_paper: dict,
        evidence_by_id: dict,
        run_id: str,
        project_id: str,
    ) -> list[dict]:
        rows = payload["rows"]
        failures = payload["paper_failures"]
        summary = payload["summary"]
        if not isinstance(failures, list) or not isinstance(summary, dict):
            raise ReviewOutlineScopeError("Evidence Matrix 摘要结构非法")
        failure_by_paper: dict[str, dict] = {}
        failed_source_ids: set[str] = set()
        for failure in failures:
            paper_id = failure.get("paper_id") if isinstance(failure, dict) else None
            source = source_by_paper.get(paper_id)
            if (
                not isinstance(failure, dict)
                or set(failure) != {"source_id", "paper_id", "error_code"}
                or not isinstance(paper_id, str)
                or failure.get("error_code") != "evidence_matrix_invalid"
                or source is None
                or failure.get("source_id") != source.source_id
                or paper_id in failure_by_paper
                or failure.get("source_id") in failed_source_ids
            ):
                raise ReviewOutlineScopeError("Evidence Matrix 失败来源不闭合")
            failure_by_paper[paper_id] = failure
            failed_source_ids.add(failure["source_id"])
        matrix_summary: list[dict] = []
        valid = 0
        for paper_id, source in source_by_paper.items():
            paper_rows = [
                row for row in rows if isinstance(row, dict) and row.get("paper_id") == paper_id
            ]
            if paper_id in failure_by_paper:
                if paper_rows:
                    raise ReviewOutlineScopeError("同一 Matrix 来源不能同时成功和失败")
                continue
            if not paper_rows:
                raise ReviewOutlineScopeError("Evidence Matrix 未覆盖当前来源")
            try:
                allowed = [
                    evidence_by_id[evidence_id]
                    for row in paper_rows
                    for evidence_id in row["evidence_ids"]
                ]
                validated = validate_evidence_matrix(
                    {"rows": paper_rows},
                    dimensions=dimensions,
                    paper_id=paper_id,
                    version_id=source.paper_version_id,
                    run_id=run_id,
                    project_id=project_id,
                    allowed_evidence=allowed,
                )
            except (EvidenceMatrixValidationError, KeyError, TypeError) as exc:
                raise ReviewOutlineScopeError("Evidence Matrix 完整复核失败") from exc
            matrix_summary.extend(
                {
                    "paper_id": row.paper_id,
                    "dimension_key": row.dimension_key,
                    "status": row.status.value,
                    "finding": row.finding,
                    "limitations": row.limitations,
                }
                for row in validated
            )
            valid += 1
        if len(rows) != valid * len(dimensions):
            raise ReviewOutlineScopeError("Evidence Matrix 包含范围外或重复 Paper 行")
        if summary != {"valid_papers": valid, "failed_papers": len(failures)}:
            raise ReviewOutlineScopeError("Evidence Matrix 覆盖统计不一致")
        if valid + len(failures) != len(source_by_paper):
            raise ReviewOutlineScopeError("Evidence Matrix 来源集合不闭合")
        return matrix_summary

    @staticmethod
    def _messages(context: dict) -> list[ChatMessage]:
        payload = {
            "prompt_version": PROMPT_VERSION,
            "research_question": context["question"],
            "dimensions": context["dimensions"],
            "evidence_matrix_summary": context["matrix_summary"],
            "paper_coverage": context["coverage"],
            "feedback_round": context["feedback_round"],
            "feedback": context["feedback"],
        }
        return [
            ChatMessage(
                role="system",
                content="生成结构化综述大纲；只能使用输入维度，不得添加证据或论文事实。",
            ),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]

    async def _persist_output(self, output: ReviewOutput, **scope) -> ReviewOutput:
        async with self._session_factory() as session:
            repo = self._review_repo_factory(session)
            if (
                await repo.get_review_run_scoped(
                    output.review_run_id, scope["project_id"], scope["owner_id"]
                )
                is None
            ):
                raise RunNotFoundError(output.review_run_id)
            persisted = await repo.get_or_add_output(output)
            self._validate_outline_output(
                persisted,
                run_id=output.review_run_id,
                version=output.version,
                idempotency_key=output.idempotency_key,
                dimension_keys=scope["dimension_keys"],
            )
            await self._complete_initial_steps(repo, output.review_run_id, persisted)
            await session.commit()
            return persisted

    @staticmethod
    def _validate_outline_output(output, *, run_id, version, idempotency_key, dimension_keys):
        if (
            output.review_run_id != run_id
            or output.output_type is not ReviewOutputType.OUTLINE
            or output.output_key != "outline"
            or output.version != version
            or output.schema_version != OUTLINE_SCHEMA_VERSION
            or output.idempotency_key != idempotency_key
        ):
            raise IdempotencyConflictError(idempotency_key)
        validate_outline(output.payload, allowed_dimension_keys=dimension_keys)
        return output

    @staticmethod
    async def _complete_initial_steps(repo: ReviewRepository, run_id: str, output: ReviewOutput):
        for key, sequence in (
            (ReviewStepKey.PROPOSE_OUTLINE, 7),
            (ReviewStepKey.PERSIST_OUTLINE, 8),
        ):
            proposed = create_run_step(
                run_id=run_id,
                step_key=key,
                sequence=sequence,
                idempotency_key=f"{run_id}:{key.value}",
                input_refs={"prompt_version": PROMPT_VERSION},
            )
            step = await repo.get_or_add_step(proposed)
            if step.status is ReviewStepStatus.PENDING:
                if output.version != 1:
                    raise ReviewOutlineScopeError("反馈轮缺少已完成的初始 Outline Step")
                running = step.start()
                if not await repo.advance_step(running, ReviewStepStatus.PENDING.value):
                    raise RunConcurrentModificationError(run_id)
                if not await repo.advance_step(
                    running.succeed({"outline_output_id": output.output_id}),
                    ReviewStepStatus.RUNNING.value,
                ):
                    raise RunConcurrentModificationError(run_id)
            elif step.status is not ReviewStepStatus.SUCCEEDED:
                raise ReviewOutlineScopeError("Outline Step 处于不可恢复状态")
            elif not isinstance(step.output_refs.get("outline_output_id"), str) or (
                output.version == 1 and step.output_refs != {"outline_output_id": output.output_id}
            ):
                raise IdempotencyConflictError(f"{run_id}:{key.value}")

    async def _persist_request_and_pause(
        self, *, output, project_id, owner_id, correlation_id, expected_previous_outline_id
    ):
        run_id = output.review_run_id
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            repo = self._review_repo_factory(session)
            run = await run_repo.get_by_id_for_update(run_id, owner_id)
            review = await repo.get_review_run_scoped_for_update(run_id, project_id, owner_id)
            if run is None or review is None or run.project_id != project_id:
                raise RunNotFoundError(run_id)
            if run.status is RunStatus.WAITING_INPUT:
                existing = await repo.get_open_human_input_request_scoped(
                    run_id, project_id, owner_id
                )
                if existing is None or existing.outline_output_id != output.output_id:
                    raise ReviewOutlineScopeError("等待状态缺少匹配的开放请求")
                if (
                    existing.request_version != output.version
                    or existing.allowed_actions != tuple(HumanInputAction)
                    or review.current_stage is not ReviewStage.REVIEW_OUTLINE
                    or review.current_outline_output_id != output.output_id
                    or output.output_type is not ReviewOutputType.OUTLINE
                    or output.output_key != "outline"
                    or output.schema_version != OUTLINE_SCHEMA_VERSION
                ):
                    raise IdempotencyConflictError(f"{run_id}:outline-request:{output.version}")
                outline_step = next(
                    (
                        item
                        for item in await repo.list_steps_scoped(run_id, project_id, owner_id)
                        if item.step_key is ReviewStepKey.REVIEW_OUTLINE
                    ),
                    None,
                )
                if (
                    outline_step is None
                    or outline_step.status is not ReviewStepStatus.PAUSED
                    or outline_step.output_refs
                    != {"request_id": existing.request_id, "outline_output_id": output.output_id}
                ):
                    raise ReviewOutlineScopeError("等待状态的 Outline Step 不闭合")
                return existing
            if run.status is not RunStatus.RUNNING:
                raise RunConcurrentModificationError(run_id)
            if review.current_outline_output_id != expected_previous_outline_id:
                raise RunConcurrentModificationError(run_id)
            proposed = create_human_input_request(
                review_run_id=run_id,
                request_version=output.version,
                outline_output_id=output.output_id,
                allowed_actions=list(HumanInputAction),
            )
            request = await repo.get_or_add_human_input_request(proposed)
            if (
                request.review_run_id != proposed.review_run_id
                or request.request_version != proposed.request_version
                or request.outline_output_id != proposed.outline_output_id
                or request.status is not HumanInputRequestStatus.OPEN
                or request.allowed_actions != proposed.allowed_actions
            ):
                raise IdempotencyConflictError(f"{run_id}:outline-request:{output.version}")
            advanced = replace(
                review,
                current_stage=ReviewStage.REVIEW_OUTLINE,
                current_outline_output_id=output.output_id,
                updated_at=datetime.now(UTC),
            )
            if not await repo.advance_review_outline(
                advanced, expected_outline_output_id=expected_previous_outline_id
            ):
                raise RunConcurrentModificationError(run_id)
            step = await repo.get_or_add_step(
                create_run_step(
                    run_id=run_id,
                    step_key=ReviewStepKey.REVIEW_OUTLINE,
                    sequence=9,
                    idempotency_key=f"{run_id}:review-outline",
                )
            )
            if step.status is ReviewStepStatus.PENDING:
                running_step = step.start()
                if not await repo.advance_step(running_step, ReviewStepStatus.PENDING.value):
                    raise RunConcurrentModificationError(run_id)
            elif step.status is ReviewStepStatus.PAUSED:
                running_step = step.resume()
                if not await repo.advance_step(running_step, ReviewStepStatus.PAUSED.value):
                    raise RunConcurrentModificationError(run_id)
            else:
                running_step = step
            if running_step.status is not ReviewStepStatus.RUNNING or not await repo.advance_step(
                running_step.pause(
                    {"request_id": request.request_id, "outline_output_id": output.output_id}
                ),
                ReviewStepStatus.RUNNING.value,
            ):
                raise RunConcurrentModificationError(run_id)
            if not await run_repo.update_status(
                run_id,
                RunStatus.RUNNING,
                RunStatus.WAITING_INPUT,
                run.event_sequence + 2,
            ):
                raise RunConcurrentModificationError(run_id)
            event_repo = self._event_repo_factory(session)
            for offset, (event_type, payload) in enumerate(
                (
                    (
                        "outline_proposed",
                        {"outline_output_id": output.output_id, "outline_version": output.version},
                    ),
                    (
                        "human_input_requested",
                        {
                            "request_id": request.request_id,
                            "request_version": request.request_version,
                            "outline_output_id": output.output_id,
                            "allowed_actions": [item.value for item in request.allowed_actions],
                        },
                    ),
                )
            ):
                await event_repo.add(
                    create_event(
                        run_id=run_id,
                        sequence=run.event_sequence + offset,
                        event_type=event_type,
                        actor_type="system",
                        correlation_id=correlation_id,
                        payload=payload,
                    )
                )
            await session.commit()
        await notify_run_event(self._event_notifier, run_id)
        return request


class HumanOutlineInputService[TSession: Session]:
    """持锁校验 HumanInput，并与 Run/Event/Outbox 原子恢复。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._event_repo_factory = event_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()
        self._resume = WaitingRunResumeService(
            session_factory=session_factory,
            run_repo_factory=run_repo_factory,
            event_repo_factory=event_repo_factory,
            outbox_repo_factory=outbox_repo_factory,
            event_notifier=self._event_notifier,
        )

    async def submit(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        request_id: str,
        request_version: int,
        outline_output_id: str,
        action: HumanInputAction | str,
        payload: dict,
        idempotency_key: str,
        correlation_id: str,
    ) -> HumanInputSubmitResult:
        if not idempotency_key or len(idempotency_key) > 255:
            raise HumanInputConflictError("human_input_idempotency_key_invalid")
        selected_action = HumanInputAction(action)
        async with self._session_factory() as session:
            repo = self._review_repo_factory(session)
            replay = await repo.get_human_input_by_idempotency_scoped(
                owner_id, idempotency_key, run_id, project_id, owner_id
            )
            if replay is not None:
                request = await repo.get_human_input_request_scoped_for_update(
                    replay.request_id, run_id, project_id, owner_id
                )
                return await self._replay_result(
                    replay,
                    request,
                    request_id,
                    request_version,
                    outline_output_id,
                    selected_action,
                    payload,
                    await repo.list_outputs_scoped(run_id, project_id, owner_id),
                )
            request = await repo.get_human_input_request_scoped_for_update(
                request_id, run_id, project_id, owner_id
            )
            if (
                request is None
                or request.status is not HumanInputRequestStatus.OPEN
                or request.request_version != request_version
                or request.outline_output_id != outline_output_id
            ):
                raise HumanInputConflictError("human_input_request_stale")
            outputs = await repo.list_outputs_scoped(run_id, project_id, owner_id)
            dimensions = self._dimension_keys(outputs)
            request_outline = next(
                (
                    item
                    for item in outputs
                    if item.output_id == request.outline_output_id
                    and item.output_type is ReviewOutputType.OUTLINE
                    and item.schema_version == OUTLINE_SCHEMA_VERSION
                ),
                None,
            )
            if request_outline is None:
                raise ReviewOutlineScopeError("HumanInput Request 的 Outline Output 不闭合")
            try:
                validate_outline(
                    request_outline.payload, allowed_dimension_keys=dimensions
                )
            except OutlineValidationError as exc:
                raise ReviewOutlineScopeError("HumanInput Request 的 Outline Output 非法") from exc
            try:
                normalized_payload, approved_id = await self._normalize_action(
                    repo, request, selected_action, payload, dimensions
                )
            except OutlineValidationError as exc:
                raise HumanInputConflictError("human_input_payload_invalid") from exc
            proposed = create_human_input(
                request=request,
                action=selected_action,
                payload=normalized_payload,
                submitted_by=owner_id,
                idempotency_key=idempotency_key,
            )
            human_input = await repo.get_or_add_human_input(proposed)
            if (
                human_input.request_id != proposed.request_id
                or human_input.request_version != proposed.request_version
                or human_input.action is not proposed.action
                or human_input.payload != proposed.payload
                or human_input.submitted_by != proposed.submitted_by
                or human_input.idempotency_key != proposed.idempotency_key
            ):
                raise HumanInputConflictError("human_input_idempotency_conflict")
            resolved = request.resolve(human_input.human_input_id)
            if not await repo.resolve_human_input_request(
                resolved, expected_status=HumanInputRequestStatus.OPEN.value
            ):
                raise HumanInputConflictError("human_input_request_resolved")
            if selected_action is HumanInputAction.EDIT:
                review = await repo.get_review_run_scoped_for_update(run_id, project_id, owner_id)
                if review is None or review.current_outline_output_id != outline_output_id:
                    raise HumanInputConflictError("outline_version_stale")
                if not await repo.advance_review_outline(
                    replace(
                        review, current_outline_output_id=approved_id, updated_at=datetime.now(UTC)
                    ),
                    expected_outline_output_id=outline_output_id,
                ):
                    raise HumanInputConflictError("outline_version_stale")
            await self._advance_review_step(
                repo,
                run_id,
                project_id,
                owner_id,
                selected_action,
                approved_id,
            )
            await self._resume.resume_in_session(
                session=session,
                run_id=run_id,
                owner_id=owner_id,
                project_id=project_id,
                reason=ResumeReason.HUMAN_INPUT_SUBMITTED,
                correlation_id=correlation_id,
                payload={
                    "request_id": request_id,
                    "request_version": request_version,
                    "human_input_id": human_input.human_input_id,
                    "action": selected_action.value,
                    "outline_output_id": outline_output_id,
                    "approved_outline_output_id": approved_id,
                },
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run_id)
        return HumanInputSubmitResult(human_input, approved_id, False)

    @staticmethod
    def _dimension_keys(outputs: list[ReviewOutput]) -> tuple[str, ...]:
        strategy = next(
            (
                item
                for item in outputs
                if item.output_type is ReviewOutputType.SEARCH_STRATEGY
                and item.schema_version == SEARCH_STRATEGY_SCHEMA_VERSION
            ),
            None,
        )
        if strategy is None or not isinstance(strategy.payload.get("dimensions"), list):
            raise ReviewOutlineScopeError("Search Strategy Output 缺失")
        try:
            keys = tuple(item["dimension_key"] for item in strategy.payload["dimensions"])
        except (KeyError, TypeError) as exc:
            raise ReviewOutlineScopeError("Search Strategy 维度非法") from exc
        if not 3 <= len(keys) <= 6 or len(set(keys)) != len(keys):
            raise ReviewOutlineScopeError("Search Strategy 维度非法")
        return keys

    @staticmethod
    async def _normalize_action(repo, request, action, payload, dimensions):
        if action not in request.allowed_actions:
            raise HumanInputConflictError("human_input_action_not_allowed")
        if action is HumanInputAction.APPROVE:
            if payload:
                raise HumanInputConflictError("approve_payload_must_be_empty")
            return {
                "approved_outline_output_id": request.outline_output_id
            }, request.outline_output_id
        if action is HumanInputAction.FEEDBACK:
            if not isinstance(payload, dict) or set(payload) != {"feedback"}:
                raise HumanInputConflictError("feedback_payload_invalid")
            feedback = validate_feedback(payload["feedback"])
            return {"feedback": feedback}, None
        if not isinstance(payload, dict):
            raise HumanInputConflictError("edit_outline_invalid")
        outline = validate_outline(payload, allowed_dimension_keys=dimensions)
        output = create_review_output(
            review_run_id=request.review_run_id,
            output_type=ReviewOutputType.OUTLINE,
            output_key="outline",
            version=request.request_version + 1,
            schema_version=OUTLINE_SCHEMA_VERSION,
            payload=outline.to_payload(),
            idempotency_key=f"{request.review_run_id}:outline-edit:{request.request_id}",
        )
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
            raise HumanInputConflictError("edit_outline_idempotency_conflict")
        return {"approved_outline_output_id": persisted.output_id}, persisted.output_id

    @staticmethod
    async def _advance_review_step(repo, run_id, project_id, owner_id, action, approved_id):
        step = next(
            (
                item
                for item in await repo.list_steps_scoped(run_id, project_id, owner_id)
                if item.step_key is ReviewStepKey.REVIEW_OUTLINE
            ),
            None,
        )
        if step is None:
            raise ReviewOutlineScopeError("Review Outline Step 缺失")
        if step.status is not ReviewStepStatus.PAUSED:
            raise HumanInputConflictError("review_outline_step_not_paused")
        running = step.resume()
        if not await repo.advance_step(running, ReviewStepStatus.PAUSED.value):
            raise RunConcurrentModificationError(run_id)
        if action is not HumanInputAction.FEEDBACK and not await repo.advance_step(
            running.succeed({"outline_output_id": approved_id}),
            ReviewStepStatus.RUNNING.value,
        ):
            raise RunConcurrentModificationError(run_id)

    @staticmethod
    async def _replay_result(
        existing, request, request_id, request_version, outline_output_id, action, payload, outputs
    ):
        if (
            request is None
            or request.status is not HumanInputRequestStatus.RESOLVED
            or request.resolved_input_id != existing.human_input_id
            or request.request_id != request_id
            or request.request_version != request_version
            or request.outline_output_id != outline_output_id
            or existing.action is not action
        ):
            raise HumanInputConflictError("human_input_idempotency_conflict")
        expected_payload = existing.payload
        if action is HumanInputAction.APPROVE:
            matches = payload == {}
            approved = expected_payload.get("approved_outline_output_id")
        elif action is HumanInputAction.FEEDBACK:
            matches = (
                isinstance(payload, dict)
                and set(payload) == {"feedback"}
                and validate_feedback(payload["feedback"]) == expected_payload.get("feedback")
            )
            approved = None
        else:
            approved = expected_payload.get("approved_outline_output_id")
            approved_output = next(
                (item for item in outputs if item.output_id == approved), None
            )
            matches = (
                approved_output is not None
                and approved_output.output_type is ReviewOutputType.OUTLINE
                and approved_output.schema_version == OUTLINE_SCHEMA_VERSION
                and validate_outline(payload, allowed_dimension_keys=None).to_payload()
                == approved_output.payload
            )
        if not matches:
            raise HumanInputConflictError("human_input_idempotency_conflict")
        return HumanInputSubmitResult(existing, approved, True)


class ReviewOutlineDecisionService[TSession: Session]:
    """在 Graph Resume 后只按持久 ID 读取并复核 HumanInput。"""

    def __init__(self, *, session_factory, review_repo_factory) -> None:
        self._session_factory = session_factory
        self._review_repo_factory = review_repo_factory

    async def load(
        self, *, run_id: str, project_id: str, owner_id: str, request_id: str, human_input_id: str
    ) -> PersistedOutlineDecision:
        async with self._session_factory() as session:
            repo = self._review_repo_factory(session)
            request = await repo.get_human_input_request_scoped_for_update(
                request_id, run_id, project_id, owner_id
            )
            human_input = await repo.get_human_input_scoped(
                human_input_id, run_id, project_id, owner_id
            )
            if (
                request is None
                or human_input is None
                or request.status is not HumanInputRequestStatus.RESOLVED
                or request.resolved_input_id != human_input_id
                or human_input.request_id != request_id
                or human_input.request_version != request.request_version
            ):
                raise ReviewOutlineScopeError("Resume HumanInput 与持久 Request 不闭合")
            outputs = await repo.list_outputs_scoped(run_id, project_id, owner_id)
            dimensions = HumanOutlineInputService._dimension_keys(outputs)
            request_outline = next(
                (
                    item
                    for item in outputs
                    if item.output_id == request.outline_output_id
                    and item.output_type is ReviewOutputType.OUTLINE
                    and item.schema_version == OUTLINE_SCHEMA_VERSION
                ),
                None,
            )
            if request_outline is None:
                raise ReviewOutlineScopeError("Resume Request 的 Outline Output 不闭合")
            try:
                validate_outline(
                    request_outline.payload, allowed_dimension_keys=dimensions
                )
            except OutlineValidationError as exc:
                raise ReviewOutlineScopeError("Resume Request 的 Outline Output 非法") from exc
            approved = human_input.payload.get("approved_outline_output_id")
            feedback = human_input.payload.get("feedback")
            if human_input.action is HumanInputAction.FEEDBACK:
                if set(human_input.payload) != {"feedback"}:
                    raise ReviewOutlineScopeError("反馈 HumanInput payload 非法")
                feedback = validate_feedback(feedback)
                approved = None
            else:
                approved_output = next(
                    (
                        item
                        for item in outputs
                        if item.output_id == approved
                        and item.output_type is ReviewOutputType.OUTLINE
                        and item.schema_version == OUTLINE_SCHEMA_VERSION
                    ),
                    None,
                )
                if approved_output is None:
                    raise ReviewOutlineScopeError("批准动作缺少持久 Outline Output")
                try:
                    validate_outline(
                        approved_output.payload, allowed_dimension_keys=dimensions
                    )
                except OutlineValidationError as exc:
                    raise ReviewOutlineScopeError("批准的 Outline Output 非法") from exc
                if human_input.action is HumanInputAction.APPROVE and (
                    set(human_input.payload) != {"approved_outline_output_id"}
                    or approved != request.outline_output_id
                ):
                    raise ReviewOutlineScopeError("approve HumanInput 与 Request 不闭合")
                if human_input.action is HumanInputAction.EDIT and (
                    set(human_input.payload) != {"approved_outline_output_id"}
                ):
                    raise ReviewOutlineScopeError("edit HumanInput 与 Outline Output 不闭合")
            return PersistedOutlineDecision(
                action=human_input.action,
                human_input_id=human_input_id,
                request_id=request_id,
                current_outline_output_id=request.outline_output_id,
                approved_outline_output_id=approved,
                feedback=feedback,
            )
