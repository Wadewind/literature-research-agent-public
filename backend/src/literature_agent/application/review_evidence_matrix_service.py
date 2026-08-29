"""Phase 3 Evidence Matrix 的上下文规划、模型提取、修复与幂等持久化。"""

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.paper_version_repository import PaperVersionRepository
from literature_agent.application.ports.parse_revision_repository import ParseRevisionRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.retriever import RetrievalResult
from literature_agent.domain.chunk import Chunk, ChunkSetStatus
from literature_agent.domain.event import create_event
from literature_agent.domain.evidence import EVIDENCE_EXCERPT_MAX_CHARS, Evidence, create_evidence
from literature_agent.domain.exceptions import (
    EvidenceMatrixInvalidError,
    EvidenceMatrixScopeError,
    IdempotencyConflictError,
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
from literature_agent.domain.review_evidence_matrix import (
    AnalysisDimension,
    EvidenceMatrixRow,
    EvidenceMatrixValidationError,
    EvidenceMatrixValidationIssue,
    parse_evidence_matrix_json,
    validate_evidence_matrix,
)
from literature_agent.domain.run import RunStatus, RunType

PROMPT_VERSION = "review-evidence-extraction.v1"
WORKFLOW_VERSION = "review.v1"
SUPPORTED_MODEL_PROFILE_VERSIONS = frozenset(
    {"review-default.v1", "review-default.v2", "review-default.v3"}
)
OUTPUT_SCHEMA_VERSION = "evidence-matrix.v1"
SEARCH_STRATEGY_SCHEMA_VERSION = "search-strategy.v1"
_PAPER_OUTPUT_PREFIX = "evidence-matrix-paper"
_FINAL_OUTPUT_KEY = "evidence-matrix"
_MAX_MODEL_OUTPUT_CHARS = 256 * 1024
_MAX_REPAIR_SOURCE_CHARS = 64 * 1024
_MAX_AGGREGATE_OUTPUT_BYTES = 240 * 1024

EVIDENCE_MATRIX_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "paper_id",
                    "dimension_key",
                    "status",
                    "finding",
                    "limitations",
                    "evidence_ids",
                ],
                "properties": {
                    "paper_id": {"type": "string"},
                    "dimension_key": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["extracted", "insufficient_evidence"],
                    },
                    "finding": {"type": ["string", "null"], "maxLength": 500},
                    "limitations": {
                        "type": ["string", "null"],
                        "maxLength": 500,
                    },
                    "evidence_ids": {
                        "type": "array",
                        "maxItems": 10,
                        "uniqueItems": True,
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}


class ReviewRetriever(Protocol):
    """Phase 2 Retriever 在长论文提取中的最小消费边界。"""

    async def retrieve_for_scope(
        self,
        *,
        owner_id: str,
        query: str,
        version_scope: list[tuple[str, str]],
        run_id: str | None = None,
    ) -> list[RetrievalResult]: ...


class MatrixModelGateway(Protocol):
    """记录调用的 ModelGateway 在本服务中的最小结构化生成边界。"""

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
    ) -> ChatResult: ...


@dataclass(frozen=True, slots=True)
class EvidenceMatrixBuildResult:
    """一次节点调用产生或复用的最终 Matrix Output。"""

    output: ReviewOutput
    valid_papers: int
    failed_papers: int
    model_invocations: int


@dataclass(frozen=True, slots=True)
class _PaperContext:
    source: ReviewSource
    parse_revision_id: str
    chunks: tuple[Chunk, ...]


class ReviewEvidenceMatrixService[TSession: Session]:
    """按固定 Profile 为每篇 ready 论文做一次正常提取并构建 Matrix。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        parse_revision_repo_factory: Callable[[TSession], ParseRevisionRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        chunk_repo_factory: Callable[[TSession], ChunkRepository],
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        retriever: ReviewRetriever,
        model_gateway: MatrixModelGateway,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._parse_revision_repo_factory = parse_revision_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._chunk_repo_factory = chunk_repo_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._event_repo_factory = event_repo_factory
        self._retriever = retriever
        self._model_gateway = model_gateway
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def build(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        search_strategy_output_id: str,
        correlation_id: str,
    ) -> EvidenceMatrixBuildResult:
        """构建或复用 Matrix；单篇无效可继续，全部无效稳定失败。"""
        (
            contexts,
            question,
            normalized_dimensions,
            threshold,
            context_limit,
            top_k,
        ) = await self._load_contexts(
            run_id=run_id,
            project_id=project_id,
            owner_id=owner_id,
            search_strategy_output_id=search_strategy_output_id,
        )
        await self._ensure_step_running(
            run_id=run_id,
            project_id=project_id,
            owner_id=owner_id,
            contexts=contexts,
            dimensions=normalized_dimensions,
        )
        existing_outputs = await self._list_outputs(run_id, project_id, owner_id)
        outputs_by_key = {item.idempotency_key: item for item in existing_outputs}
        final_key = f"{run_id}:{_FINAL_OUTPUT_KEY}:{PROMPT_VERSION}"
        existing_final = outputs_by_key.get(final_key)
        if existing_final is not None:
            valid_papers, failed_papers = await self._validate_final_output(
                existing_final,
                contexts=contexts,
                dimensions=normalized_dimensions,
                run_id=run_id,
                project_id=project_id,
            )
            return EvidenceMatrixBuildResult(
                output=existing_final,
                valid_papers=valid_papers,
                failed_papers=failed_papers,
                model_invocations=0,
            )
        valid_rows: list[EvidenceMatrixRow] = []
        failures: list[dict[str, str]] = []
        model_invocations = 0
        for context in contexts:
            key = self._paper_idempotency_key(run_id, context.source.source_id)
            existing = outputs_by_key.get(key)
            if existing is not None:
                failure = self._validate_persisted_paper_failure(existing, context=context)
                if failure is not None:
                    failures.append(failure)
                    continue
                rows = await self._validate_persisted_output(
                    existing,
                    context=context,
                    dimensions=normalized_dimensions,
                    run_id=run_id,
                    project_id=project_id,
                )
                valid_rows.extend(rows)
                continue
            selected = await self._select_chunks(
                context=context,
                dimensions=normalized_dimensions,
                owner_id=owner_id,
                run_id=run_id,
                threshold=threshold,
                context_limit=context_limit,
                top_k=top_k,
            )
            evidence = await self._persist_evidence(
                run_id=run_id,
                project_id=project_id,
                owner_id=owner_id,
                context=context,
                chunks=selected,
            )
            messages = self._extraction_messages(
                question=question,
                dimensions=normalized_dimensions,
                source=context.source,
                chunks=selected,
                evidence=evidence,
            )
            first = await self._model_gateway.generate(
                messages,
                json_schema=EVIDENCE_MATRIX_JSON_SCHEMA,
                max_tokens=4_000,
                run_id=run_id,
            )
            model_invocations += 1
            try:
                rows = self._parse_and_validate(
                    first.content,
                    dimensions=normalized_dimensions,
                    context=context,
                    run_id=run_id,
                    project_id=project_id,
                    evidence=evidence,
                )
            except EvidenceMatrixValidationError as first_error:
                repair = await self._model_gateway.generate(
                    self._repair_messages(messages, first.content, first_error),
                    json_schema=EVIDENCE_MATRIX_JSON_SCHEMA,
                    max_tokens=4_000,
                    run_id=run_id,
                )
                model_invocations += 1
                try:
                    rows = self._parse_and_validate(
                        repair.content,
                        dimensions=normalized_dimensions,
                        context=context,
                        run_id=run_id,
                        project_id=project_id,
                        evidence=evidence,
                    )
                except EvidenceMatrixValidationError:
                    failure_output = create_review_output(
                        review_run_id=run_id,
                        output_type=ReviewOutputType.EVIDENCE_MATRIX,
                        output_key=f"paper:{context.source.source_id}",
                        version=1,
                        schema_version=OUTPUT_SCHEMA_VERSION,
                        payload=self._paper_failure_payload(context),
                        idempotency_key=key,
                    )
                    persisted_failure = await self._persist_output(
                        failure_output, project_id=project_id, owner_id=owner_id
                    )
                    failure = self._validate_persisted_paper_failure(
                        persisted_failure, context=context
                    )
                    if failure is None:  # pragma: no cover - 上述固定 payload 的防御性分支
                        raise EvidenceMatrixScopeError(
                            "单篇 Matrix 失败 Output 结构非法"
                        ) from None
                    failures.append(failure)
                    continue
            output = create_review_output(
                review_run_id=run_id,
                output_type=ReviewOutputType.EVIDENCE_MATRIX,
                output_key=f"paper:{context.source.source_id}",
                version=1,
                schema_version=OUTPUT_SCHEMA_VERSION,
                payload={"rows": [row.to_payload() for row in rows]},
                idempotency_key=key,
            )
            persisted = await self._persist_output(output, project_id=project_id, owner_id=owner_id)
            valid_rows.extend(
                await self._validate_persisted_output(
                    persisted,
                    context=context,
                    dimensions=normalized_dimensions,
                    run_id=run_id,
                    project_id=project_id,
                )
            )
        if not valid_rows:
            await self._fail_step(run_id, project_id, owner_id, "evidence_matrix_invalid")
            raise EvidenceMatrixInvalidError()
        final_payload = {
            "rows": [row.to_payload() for row in valid_rows],
            "paper_failures": failures,
            "summary": {
                "valid_papers": len(contexts) - len(failures),
                "failed_papers": len(failures),
            },
        }
        if (
            len(json.dumps(final_payload, ensure_ascii=False).encode())
            > _MAX_AGGREGATE_OUTPUT_BYTES
        ):
            await self._fail_step(run_id, project_id, owner_id, "evidence_matrix_too_large")
            raise EvidenceMatrixInvalidError("evidence_matrix_too_large")
        final = create_review_output(
            review_run_id=run_id,
            output_type=ReviewOutputType.EVIDENCE_MATRIX,
            output_key=_FINAL_OUTPUT_KEY,
            version=1,
            schema_version=OUTPUT_SCHEMA_VERSION,
            payload=final_payload,
            idempotency_key=final_key,
        )
        final = await self._complete_matrix(
            final,
            project_id=project_id,
            owner_id=owner_id,
            correlation_id=correlation_id,
            valid_papers=len(contexts) - len(failures),
            failed_papers=len(failures),
        )
        return EvidenceMatrixBuildResult(
            output=final,
            valid_papers=len(contexts) - len(failures),
            failed_papers=len(failures),
            model_invocations=model_invocations,
        )

    async def _load_contexts(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        search_strategy_output_id: str,
    ) -> tuple[
        list[_PaperContext],
        str,
        tuple[AnalysisDimension, ...],
        int,
        int,
        int,
    ]:
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
            review_repo = self._review_repo_factory(session)
            review = await review_repo.get_review_run_scoped(run_id, project_id, owner_id)
            if (
                run is None
                or run.owner_id != owner_id
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
                or review is None
            ):
                raise RunNotFoundError(run_id)
            if (
                review.workflow_version != WORKFLOW_VERSION
                or review.model_profile_version not in SUPPORTED_MODEL_PROFILE_VERSIONS
                or review.prompt_versions.get("evidence_extract") != PROMPT_VERSION
            ):
                raise EvidenceMatrixScopeError(
                    "Review Run 的 Workflow/Model Profile/Prompt 版本不受支持"
                )
            outputs = await review_repo.list_outputs_scoped(run_id, project_id, owner_id)
            strategy = next(
                (
                    item
                    for item in outputs
                    if item.output_id == search_strategy_output_id
                    and item.output_type is ReviewOutputType.SEARCH_STRATEGY
                    and item.schema_version == SEARCH_STRATEGY_SCHEMA_VERSION
                ),
                None,
            )
            if strategy is None:
                raise EvidenceMatrixScopeError("Search Strategy Output 不属于当前 Review Run")
            dimensions = self._parse_dimensions(strategy.payload)
            sources = await review_repo.list_sources_scoped(run_id, project_id, owner_id)
            ready = [item for item in sources if item.status is ReviewSourceStatus.READY]
            if not ready:
                raise EvidenceMatrixScopeError("Review Run 没有 ready 来源")
            contexts: list[_PaperContext] = []
            version_repo = self._paper_version_repo_factory(session)
            chunk_set_repo = self._chunk_set_repo_factory(session)
            revision_repo = self._parse_revision_repo_factory(session)
            chunk_repo = self._chunk_repo_factory(session)
            for source in ready:
                if not source.paper_id or not source.paper_version_id:
                    raise EvidenceMatrixScopeError("ready 来源缺少 Paper/Version")
                version = await version_repo.get_by_id(source.paper_version_id)
                chunk_set = await chunk_set_repo.get_ready_by_version(source.paper_version_id)
                if (
                    version is None
                    or version.owner_id != owner_id
                    or version.paper_id != source.paper_id
                    or chunk_set is None
                    or chunk_set.status is not ChunkSetStatus.READY
                ):
                    raise EvidenceMatrixScopeError("ready 来源的 PaperVersion/ChunkSet 归属非法")
                revision = await revision_repo.get_by_id(chunk_set.parse_revision_id)
                if (
                    revision is None
                    or revision.version_id != source.paper_version_id
                    or revision.status is not ParseRevisionStatus.SUCCEEDED
                ):
                    raise EvidenceMatrixScopeError("ready ChunkSet 的 ParseRevision 归属非法")
                chunks = tuple(await chunk_repo.list_by_chunk_set(chunk_set.chunk_set_id))
                if not chunks or any(
                    item.chunk_set_id != chunk_set.chunk_set_id for item in chunks
                ):
                    raise EvidenceMatrixScopeError("ready ChunkSet 没有合法 Chunk")
                contexts.append(_PaperContext(source, revision.revision_id, chunks))
            if len({item.source.paper_id for item in contexts}) != len(contexts):
                raise EvidenceMatrixScopeError("ready 来源包含重复 Paper，Matrix 行无法唯一定位")
            config = review.config_snapshot
            source_limit = self._positive_config(config, "source_limit")
            if len(contexts) > source_limit:
                raise EvidenceMatrixScopeError("ready 来源数量超过 Review Profile 上限")
            return (
                contexts,
                review.research_question,
                dimensions,
                self._positive_config(config, "full_text_token_threshold"),
                self._positive_config(config, "evidence_context_token_limit"),
                self._positive_config(config, "retrieval_top_k_per_dimension"),
            )

    @staticmethod
    def _parse_dimensions(payload: dict) -> tuple[AnalysisDimension, ...]:
        raw = payload.get("dimensions")
        if not isinstance(raw, list) or not 3 <= len(raw) <= 6:
            raise EvidenceMatrixScopeError("Search Strategy 必须包含 3 到 6 个维度")
        try:
            dimensions = tuple(
                AnalysisDimension(
                    dimension_key=item["dimension_key"],
                    name=item["name"],
                    extraction_question=item["extraction_question"],
                )
                for item in raw
                if isinstance(item, dict)
                and set(item) == {"dimension_key", "name", "extraction_question"}
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceMatrixScopeError("Search Strategy 维度结构非法") from exc
        if len(dimensions) != len(raw) or len({item.dimension_key for item in dimensions}) != len(
            dimensions
        ):
            raise EvidenceMatrixScopeError("Search Strategy 维度结构非法或重复")
        return dimensions

    @staticmethod
    def _positive_config(config: dict, key: str) -> int:
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise EvidenceMatrixScopeError(f"Review Profile 缺少合法配置 {key}")
        return value

    async def _select_chunks(
        self,
        *,
        context: _PaperContext,
        dimensions: tuple[AnalysisDimension, ...],
        owner_id: str,
        run_id: str,
        threshold: int,
        context_limit: int,
        top_k: int,
    ) -> tuple[Chunk, ...]:
        if sum(item.token_count for item in context.chunks) <= threshold:
            return context.chunks
        by_id = {item.chunk_id: item for item in context.chunks}
        selected: dict[str, Chunk] = {}
        scope = [(context.source.paper_id or "", context.source.paper_version_id or "")]
        for dimension in dimensions:
            results = await self._retriever.retrieve_for_scope(
                owner_id=owner_id,
                query=dimension.extraction_question,
                version_scope=scope,
                run_id=run_id,
            )
            for result in results[:top_k]:
                if (
                    result.paper_id != context.source.paper_id
                    or result.version_id != context.source.paper_version_id
                    or result.chunk.chunk_id not in by_id
                    or result.chunk.chunk_set_id != context.chunks[0].chunk_set_id
                ):
                    raise EvidenceMatrixScopeError(
                        "Retriever 返回了当前 PaperVersion 范围外的 Chunk"
                    )
                selected.setdefault(result.chunk.chunk_id, by_id[result.chunk.chunk_id])
        used = 0
        limited: list[Chunk] = []
        for chunk in sorted(selected.values(), key=lambda item: item.sequence):
            if used + chunk.token_count > context_limit:
                continue
            used += chunk.token_count
            limited.append(chunk)
        return tuple(limited)

    async def _persist_evidence(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        context: _PaperContext,
        chunks: tuple[Chunk, ...],
    ) -> list[Evidence]:
        async with self._session_factory() as session:
            source = await self._review_repo_factory(session).get_source_scoped_for_update(
                context.source.source_id, run_id, project_id, owner_id
            )
            if (
                source is None
                or source != context.source
                or source.status is not ReviewSourceStatus.READY
            ):
                raise EvidenceMatrixScopeError("Evidence 写入前来源范围发生变化")
            proposals = [
                create_evidence(
                    run_id=run_id,
                    project_id=project_id,
                    paper_id=source.paper_id or "",
                    version_id=source.paper_version_id or "",
                    parse_revision_id=context.parse_revision_id,
                    chunk_id=chunk.chunk_id,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    excerpt=chunk.text[:EVIDENCE_EXCERPT_MAX_CHARS],
                )
                for chunk in chunks
            ]
            evidence = await self._evidence_repo_factory(session).get_or_add_many(proposals)
            proposed_by_chunk = {item.chunk_id: item for item in proposals}
            for item in evidence:
                proposed = proposed_by_chunk[item.chunk_id]
                if (
                    item.run_id != proposed.run_id
                    or item.project_id != proposed.project_id
                    or item.paper_id != proposed.paper_id
                    or item.version_id != proposed.version_id
                    or item.parse_revision_id != proposed.parse_revision_id
                    or item.chunk_id != proposed.chunk_id
                    or item.section_path != proposed.section_path
                    or item.page_start != proposed.page_start
                    or item.page_end != proposed.page_end
                    or item.excerpt != proposed.excerpt
                ):
                    raise EvidenceMatrixScopeError(
                        "既有 Evidence 与当前 Review Source/Chunk 语义冲突"
                    )
            await session.commit()
            return evidence

    @staticmethod
    def _extraction_messages(
        *,
        question: str,
        dimensions: tuple[AnalysisDimension, ...],
        source: ReviewSource,
        chunks: tuple[Chunk, ...],
        evidence: list[Evidence],
    ) -> list[ChatMessage]:
        evidence_by_chunk = {item.chunk_id: item for item in evidence}
        metadata = {
            "paper_id": source.paper_id,
            "paper_version_id": source.paper_version_id,
            "arxiv_id": source.arxiv_id,
            "arxiv_version": source.arxiv_version,
            "title": source.metadata_snapshot.get("title"),
            "authors": source.metadata_snapshot.get("authors"),
            "published_at": source.metadata_snapshot.get("published_at"),
        }
        payload = {
            "prompt_version": PROMPT_VERSION,
            "research_question": question,
            "dimensions": [
                {
                    "dimension_key": item.dimension_key,
                    "name": item.name,
                    "extraction_question": item.extraction_question,
                }
                for item in dimensions
            ],
            "paper": metadata,
            "evidence_context": [
                {
                    "evidence_id": evidence_by_chunk[chunk.chunk_id].evidence_id,
                    "chunk_id": chunk.chunk_id,
                    "sequence": chunk.sequence,
                    "section_path": chunk.section_path,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "text": chunk.text,
                }
                for chunk in chunks
            ],
        }
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是 Evidence Matrix 提取器。每个维度只输出一行；只能引用输入中的 "
                    "evidence_id。证据不足时使用 insufficient_evidence 并清空其他字段。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]

    @staticmethod
    def _repair_messages(
        extraction_messages: list[ChatMessage],
        original: str,
        error: EvidenceMatrixValidationError,
    ) -> list[ChatMessage]:
        payload = {
            "prompt_version": PROMPT_VERSION,
            "validation_errors": [item.to_payload() for item in error.issues],
        }
        return [
            *extraction_messages,
            ChatMessage(role="assistant", content=original[:_MAX_REPAIR_SOURCE_CHARS]),
            ChatMessage(
                role="user",
                content=(
                    "只修复上述输出的结构和引用错误，不添加未提供的 Evidence。\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            ),
        ]

    @staticmethod
    def _parse_and_validate(
        content: str,
        *,
        dimensions: tuple[AnalysisDimension, ...],
        context: _PaperContext,
        run_id: str,
        project_id: str,
        evidence: list[Evidence],
    ) -> tuple[EvidenceMatrixRow, ...]:
        if len(content) > _MAX_MODEL_OUTPUT_CHARS:
            raise EvidenceMatrixValidationError(
                [EvidenceMatrixValidationIssue("output_too_large", "$", "模型输出超过大小上限")]
            )
        return validate_evidence_matrix(
            parse_evidence_matrix_json(content),
            dimensions=dimensions,
            paper_id=context.source.paper_id or "",
            version_id=context.source.paper_version_id or "",
            run_id=run_id,
            project_id=project_id,
            allowed_evidence=evidence,
        )

    async def _ensure_step_running(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        contexts: list[_PaperContext],
        dimensions: tuple[AnalysisDimension, ...],
    ) -> None:
        """创建或复用总 Matrix Step；不为每篇论文伪造固定图 Step key。"""
        proposed = create_run_step(
            run_id=run_id,
            step_key=ReviewStepKey.BUILD_EVIDENCE_MATRIX,
            sequence=6,
            idempotency_key=f"{run_id}:build-evidence-matrix:{PROMPT_VERSION}",
            input_refs={
                "source_ids": [item.source.source_id for item in contexts],
                "dimension_keys": [item.dimension_key for item in dimensions],
                "prompt_version": PROMPT_VERSION,
            },
        )
        async with self._session_factory() as session:
            repository = self._review_repo_factory(session)
            if await repository.get_review_run_scoped(run_id, project_id, owner_id) is None:
                raise RunNotFoundError(run_id)
            step = await repository.get_or_add_step(proposed)
            if (
                step.step_key != proposed.step_key
                or step.sequence != proposed.sequence
                or step.input_refs != proposed.input_refs
            ):
                raise IdempotencyConflictError(proposed.idempotency_key)
            if step.status is ReviewStepStatus.PENDING:
                if not await repository.advance_step(step.start(), ReviewStepStatus.PENDING.value):
                    current = next(
                        (
                            item
                            for item in await repository.list_steps_scoped(
                                run_id, project_id, owner_id
                            )
                            if item.step_id == step.step_id
                        ),
                        None,
                    )
                    if current is None or current.status not in {
                        ReviewStepStatus.RUNNING,
                        ReviewStepStatus.SUCCEEDED,
                    }:
                        raise EvidenceMatrixScopeError("Matrix Step 并发推进失败")
            elif step.status not in {
                ReviewStepStatus.RUNNING,
                ReviewStepStatus.SUCCEEDED,
            }:
                raise EvidenceMatrixScopeError("Matrix Step 已处于不可恢复终态")
            await session.commit()

    async def _fail_step(
        self, run_id: str, project_id: str, owner_id: str, error_code: str
    ) -> None:
        """在永久 Matrix 失败时收尾总 Step；Run 终态由执行服务统一处理。"""
        async with self._session_factory() as session:
            repository = self._review_repo_factory(session)
            steps = await repository.list_steps_scoped(run_id, project_id, owner_id)
            step = next(
                (item for item in steps if item.step_key is ReviewStepKey.BUILD_EVIDENCE_MATRIX),
                None,
            )
            if step is None:
                raise EvidenceMatrixScopeError("Matrix Step 不存在")
            if step.status in {
                ReviewStepStatus.PENDING,
                ReviewStepStatus.RUNNING,
            } and not await repository.advance_step(step.fail(error_code), step.status.value):
                raise EvidenceMatrixScopeError("Matrix Step 失败收尾发生竞争")
            await session.commit()

    async def _complete_matrix(
        self,
        output: ReviewOutput,
        *,
        project_id: str,
        owner_id: str,
        correlation_id: str,
        valid_papers: int,
        failed_papers: int,
    ) -> ReviewOutput:
        """原子提交聚合 Output、Step 成功与 completed Event；重放不重复事件。"""
        emitted = False
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(output.review_run_id, owner_id)
            repository = self._review_repo_factory(session)
            review = await repository.get_review_run_scoped_for_update(
                output.review_run_id, project_id, owner_id
            )
            if (
                run is None
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
                or review is None
            ):
                raise RunNotFoundError(output.review_run_id)
            existing_outputs = await repository.list_outputs_scoped(
                output.review_run_id, project_id, owner_id
            )
            existing = next(
                (
                    item
                    for item in existing_outputs
                    if item.idempotency_key == output.idempotency_key
                ),
                None,
            )
            persisted = await repository.get_or_add_output(output)
            if (
                persisted.review_run_id != output.review_run_id
                or persisted.idempotency_key != output.idempotency_key
                or persisted.output_type != output.output_type
                or persisted.output_key != output.output_key
                or persisted.version != output.version
                or persisted.schema_version != output.schema_version
                or persisted.payload != output.payload
            ):
                raise IdempotencyConflictError(output.idempotency_key)
            steps = await repository.list_steps_scoped(output.review_run_id, project_id, owner_id)
            step = next(
                (item for item in steps if item.step_key is ReviewStepKey.BUILD_EVIDENCE_MATRIX),
                None,
            )
            if step is None:
                raise EvidenceMatrixScopeError("Matrix Step 不存在")
            refs = {"evidence_matrix_output_id": persisted.output_id}
            if step.status is ReviewStepStatus.RUNNING:
                if not await repository.advance_step(
                    step.succeed(refs), ReviewStepStatus.RUNNING.value
                ):
                    raise EvidenceMatrixScopeError("Matrix Step 完成发生竞争")
            elif step.status is not ReviewStepStatus.SUCCEEDED or step.output_refs != refs:
                raise IdempotencyConflictError(step.idempotency_key)
            if existing is None and review.current_stage in {
                ReviewStage.VALIDATE_REQUEST,
                ReviewStage.FORMULATE_SEARCH_STRATEGY,
                ReviewStage.SEARCH_ARXIV,
                ReviewStage.IMPORT_ARXIV_PAPERS,
                ReviewStage.WAIT_FOR_INGESTION,
                ReviewStage.BUILD_EVIDENCE_MATRIX,
            }:
                advanced = replace(
                    review,
                    current_stage=ReviewStage.PROPOSE_OUTLINE,
                    updated_at=datetime.now(UTC),
                )
                if not await repository.advance_review_stage(
                    advanced, expected_stage=review.current_stage.value
                ):
                    raise EvidenceMatrixScopeError("Matrix Stage 完成发生竞争")
            if existing is None:
                if not await run_repo.update_status(
                    run.run_id,
                    RunStatus(run.status),
                    RunStatus(run.status),
                    run.event_sequence + 1,
                ):
                    raise EvidenceMatrixScopeError("Matrix completed Event sequence 竞争失败")
                await self._event_repo_factory(session).add(
                    create_event(
                        run_id=run.run_id,
                        sequence=run.event_sequence,
                        event_type="evidence_matrix_completed",
                        actor_type="system",
                        correlation_id=correlation_id,
                        payload={
                            "output_id": persisted.output_id,
                            "valid_papers": valid_papers,
                            "failed_papers": failed_papers,
                        },
                    )
                )
                emitted = True
            await session.commit()
        if emitted:
            await notify_run_event(self._event_notifier, output.review_run_id)
        return persisted

    async def _list_outputs(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewOutput]:
        async with self._session_factory() as session:
            return await self._review_repo_factory(session).list_outputs_scoped(
                run_id, project_id, owner_id
            )

    async def _persist_output(
        self, output: ReviewOutput, *, project_id: str, owner_id: str
    ) -> ReviewOutput:
        async with self._session_factory() as session:
            repository = self._review_repo_factory(session)
            if (
                await repository.get_review_run_scoped(output.review_run_id, project_id, owner_id)
                is None
            ):
                raise RunNotFoundError(output.review_run_id)
            persisted = await repository.get_or_add_output(output)
            if (
                persisted.review_run_id != output.review_run_id
                or persisted.idempotency_key != output.idempotency_key
                or persisted.output_type != output.output_type
                or persisted.output_key != output.output_key
                or persisted.version != output.version
                or persisted.schema_version != output.schema_version
                or persisted.payload != output.payload
            ):
                raise IdempotencyConflictError(output.idempotency_key)
            await session.commit()
            return persisted

    async def _validate_persisted_output(
        self,
        output: ReviewOutput,
        *,
        context: _PaperContext,
        dimensions: tuple[AnalysisDimension, ...],
        run_id: str,
        project_id: str,
    ) -> tuple[EvidenceMatrixRow, ...]:
        self._validate_paper_output_identity(output, context=context)
        raw_ids = [
            evidence_id
            for row in output.payload.get("rows", [])
            if isinstance(row, dict)
            for evidence_id in row.get("evidence_ids", [])
            if isinstance(evidence_id, str)
        ]
        async with self._session_factory() as session:
            evidence = await self._evidence_repo_factory(session).list_by_ids(raw_ids)
        try:
            return validate_evidence_matrix(
                output.payload,
                dimensions=dimensions,
                paper_id=context.source.paper_id or "",
                version_id=context.source.paper_version_id or "",
                run_id=run_id,
                project_id=project_id,
                allowed_evidence=evidence,
            )
        except EvidenceMatrixValidationError as exc:
            raise EvidenceMatrixScopeError("既有 Matrix Output 未通过范围复核") from exc

    @staticmethod
    def _paper_failure_payload(context: _PaperContext) -> dict[str, str]:
        return {
            "status": "failed",
            "source_id": context.source.source_id,
            "paper_id": context.source.paper_id or "",
            "error_code": "evidence_matrix_invalid",
        }

    @classmethod
    def _validate_persisted_paper_failure(
        cls, output: ReviewOutput, *, context: _PaperContext
    ) -> dict[str, str] | None:
        """识别并复核稳定单篇失败事实；成功 Output 返回 None。"""
        cls._validate_paper_output_identity(output, context=context)
        if "status" not in output.payload:
            return None
        expected = cls._paper_failure_payload(context)
        if output.payload != expected:
            raise EvidenceMatrixScopeError("既有单篇 Matrix 失败 Output 语义冲突")
        return {
            "source_id": expected["source_id"],
            "paper_id": expected["paper_id"],
            "error_code": expected["error_code"],
        }

    @staticmethod
    def _validate_paper_output_identity(
        output: ReviewOutput, *, context: _PaperContext
    ) -> None:
        if (
            output.output_type is not ReviewOutputType.EVIDENCE_MATRIX
            or output.output_key != f"paper:{context.source.source_id}"
            or output.version != 1
            or output.schema_version != OUTPUT_SCHEMA_VERSION
        ):
            raise EvidenceMatrixScopeError("既有单篇 Matrix Output 身份或版本不受支持")

    async def _validate_final_output(
        self,
        output: ReviewOutput,
        *,
        contexts: list[_PaperContext],
        dimensions: tuple[AnalysisDimension, ...],
        run_id: str,
        project_id: str,
    ) -> tuple[int, int]:
        """重放前复核聚合 Output 闭包，避免失败论文再次调用模型。"""
        if (
            output.output_type is not ReviewOutputType.EVIDENCE_MATRIX
            or output.output_key != _FINAL_OUTPUT_KEY
            or output.version != 1
            or output.schema_version != OUTPUT_SCHEMA_VERSION
        ):
            raise EvidenceMatrixScopeError("既有聚合 Matrix Output 身份或版本不受支持")
        rows = output.payload.get("rows")
        failures = output.payload.get("paper_failures")
        summary = output.payload.get("summary")
        if (
            not isinstance(rows, list)
            or not isinstance(failures, list)
            or not isinstance(summary, dict)
        ):
            raise EvidenceMatrixScopeError("既有聚合 Matrix 结构非法")
        failure_by_source: dict[str, dict] = {}
        for failure in failures:
            if (
                not isinstance(failure, dict)
                or set(failure) != {"source_id", "paper_id", "error_code"}
                or failure.get("error_code") != "evidence_matrix_invalid"
                or not isinstance(failure.get("source_id"), str)
            ):
                raise EvidenceMatrixScopeError("既有聚合 Matrix 失败摘要非法")
            failure_by_source[failure["source_id"]] = failure
        valid = 0
        for context in contexts:
            paper_rows = [
                row
                for row in rows
                if isinstance(row, dict) and row.get("paper_id") == context.source.paper_id
            ]
            failure = failure_by_source.get(context.source.source_id)
            if paper_rows and failure is not None:
                raise EvidenceMatrixScopeError("同一来源不能同时成功和失败")
            if failure is not None:
                if failure.get("paper_id") != context.source.paper_id:
                    raise EvidenceMatrixScopeError("失败摘要的 Paper 范围非法")
                continue
            if not paper_rows:
                raise EvidenceMatrixScopeError("聚合 Matrix 未覆盖当前 ready 来源")
            raw_ids = [
                evidence_id
                for row in paper_rows
                for evidence_id in row.get("evidence_ids", [])
                if isinstance(evidence_id, str)
            ]
            async with self._session_factory() as session:
                evidence = await self._evidence_repo_factory(session).list_by_ids(raw_ids)
            try:
                validate_evidence_matrix(
                    {"rows": paper_rows},
                    dimensions=dimensions,
                    paper_id=context.source.paper_id or "",
                    version_id=context.source.paper_version_id or "",
                    run_id=run_id,
                    project_id=project_id,
                    allowed_evidence=evidence,
                )
            except EvidenceMatrixValidationError as exc:
                raise EvidenceMatrixScopeError("聚合 Matrix 引用范围复核失败") from exc
            valid += 1
        if len(failure_by_source) != len(failures) or valid + len(failures) != len(contexts):
            raise EvidenceMatrixScopeError("聚合 Matrix 来源集合不闭合")
        if summary != {"valid_papers": valid, "failed_papers": len(failures)}:
            raise EvidenceMatrixScopeError("聚合 Matrix 统计摘要不一致")
        return valid, len(failures)

    @staticmethod
    def _paper_idempotency_key(run_id: str, source_id: str) -> str:
        return f"{run_id}:{_PAPER_OUTPUT_PREFIX}:{source_id}:{PROMPT_VERSION}"
