"""最终综述 Artifact 的确定性导出、幂等持久化与 Run 收尾。"""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.claim_set_repository import ClaimSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.model_invocation_repository import (
    ModelInvocationRepository,
)
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    IdempotencyConflictError,
    ReviewExportInvalidError,
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.review import (
    Artifact,
    ArtifactType,
    ReviewOutput,
    ReviewOutputType,
    ReviewSourceStatus,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    create_artifact,
    create_review_output,
    create_run_step,
)
from literature_agent.domain.review_export import build_review_export
from literature_agent.domain.review_search_strategy import (
    parse_search_strategy,
    validate_search_strategy,
)
from literature_agent.domain.review_section import (
    parse_section_draft_json,
    validate_section_draft,
)
from literature_agent.domain.run import RunStatus, RunType

TSession = TypeVar("TSession", bound=Session)

FINAL_REVIEW_SCHEMA_VERSION = "final-review.v1"
EXPORT_VERSION = "review-markdown.v1"
_ARTIFACT_SPECS = (
    (ArtifactType.REVIEW_MARKDOWN, "review.md", "text/markdown; charset=utf-8"),
    (ArtifactType.SEARCH_STRATEGY, "search-strategy.json", "application/json"),
    (ArtifactType.SOURCE_MANIFEST, "sources.json", "application/json"),
    (ArtifactType.EVIDENCE_MATRIX, "evidence-matrix.json", "application/json"),
    (ArtifactType.BIBLIOGRAPHY, "references.json", "application/json"),
    (ArtifactType.RUN_SUMMARY, "run-summary.json", "application/json"),
)


@dataclass(frozen=True, slots=True)
class ReviewExportResult:
    final_output: ReviewOutput
    artifacts: tuple[Artifact, ...]

    @property
    def markdown_artifact(self) -> Artifact:
        return next(
            item for item in self.artifacts if item.artifact_type is ArtifactType.REVIEW_MARKDOWN
        )


class ReviewExportService[TSession: Session]:
    """以 PostgreSQL 事实组装并提交最终 Artifact，不调用模型。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        claim_set_repo_factory: Callable[[TSession], ClaimSetRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        model_invocation_repo_factory: Callable[[TSession], ModelInvocationRepository],
        storage: Storage,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._claim_set_repo_factory = claim_set_repo_factory
        self._event_repo_factory = event_repo_factory
        self._model_invocation_repo_factory = model_invocation_repo_factory
        self._storage = storage
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def export(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        approved_outline_output_id: str,
        evidence_matrix_output_id: str,
        section_output_ids: list[str],
        claim_set_id: str,
        consistency_output_id: str,
        correlation_id: str,
    ) -> ReviewExportResult:
        """事务外写稳定文件，随后持锁原子提交 Output/Artifact/Step/Event。"""
        context = await self._load_context(
            run_id,
            project_id,
            owner_id,
            approved_outline_output_id,
            evidence_matrix_output_id,
            section_output_ids,
            claim_set_id,
            consistency_output_id,
        )
        export = build_review_export(
            research_question=context["review"].research_question,
            sections=context["sections"],
            claims=context["claims"],
            citations=context["citations"],
            evidence=context["evidence"],
            sources=context["sources"],
        )
        statistics = self._statistics(context)
        contents = self._artifact_contents(
            context, export.markdown, export.reference_mapping, statistics
        )
        manifest = []
        for artifact_type, filename, media_type in _ARTIFACT_SPECS:
            content = contents[artifact_type]
            digest = hashlib.sha256(content).hexdigest()
            key = f"{owner_id}/reviews/{run_id}/{digest}/{filename}"
            manifest.append(
                {
                    "artifact_type": artifact_type.value,
                    "storage_key": key,
                    "content_hash": digest,
                    "size_bytes": len(content),
                    "media_type": media_type,
                }
            )
            # 文件缓存可早于数据库事实；重放使用同一 key/bytes 安全覆盖。
            await self._storage.write(key, content)

        final_payload = {
            "approved_outline_output_id": approved_outline_output_id,
            "evidence_matrix_output_id": evidence_matrix_output_id,
            "section_output_ids": list(section_output_ids),
            "claim_set_id": claim_set_id,
            "consistency_output_id": consistency_output_id,
            "reference_count": len(export.reference_mapping),
            "artifact_manifest": manifest,
            "statistics": statistics,
        }
        proposed_output = create_review_output(
            review_run_id=run_id,
            output_type=ReviewOutputType.FINAL_REVIEW,
            output_key="final-review",
            version=1,
            schema_version=FINAL_REVIEW_SCHEMA_VERSION,
            payload=final_payload,
            idempotency_key=f"{run_id}:final-review:{EXPORT_VERSION}",
        )
        result = await self._commit_export(
            proposed_output,
            manifest,
            project_id=project_id,
            owner_id=owner_id,
            correlation_id=correlation_id,
        )
        if result[1]:
            await notify_run_event(self._event_notifier, run_id)
        return ReviewExportResult(result[0], result[2])

    async def finalize(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        final_artifact_id: str,
        correlation_id: str,
    ) -> None:
        """在导出闭包完备后一次性提交 Run SUCCEEDED 与业务 Event。"""
        notify = False
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(run_id, owner_id)
            if run is None or run.project_id != project_id or run.run_type != RunType.REVIEW.value:
                raise RunNotFoundError(run_id)
            if run.status is RunStatus.SUCCEEDED:
                return
            if run.status is not RunStatus.RUNNING:
                raise RunNotFoundError(run_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped_for_update(run_id, project_id, owner_id)
            artifacts = await repo.list_artifacts_scoped(run_id, project_id, owner_id)
            export_step = _step(
                await repo.list_steps_scoped(run_id, project_id, owner_id),
                ReviewStepKey.EXPORT_REVIEW,
            )
            if (
                review is None
                or review.current_stage is not ReviewStage.FINALIZE
                or review.final_artifact_id != final_artifact_id
                or export_step.status is not ReviewStepStatus.SUCCEEDED
                or final_artifact_id not in {item.artifact_id for item in artifacts}
            ):
                raise ReviewExportInvalidError("review_finalize_scope_invalid")
            final_step = await repo.get_or_add_step(
                create_run_step(
                    run_id=run_id,
                    step_key=ReviewStepKey.FINALIZE,
                    sequence=14,
                    idempotency_key=f"{run_id}:finalize",
                    input_refs={"final_artifact_id": final_artifact_id},
                )
            )
            if final_step.status is ReviewStepStatus.PENDING:
                final_step = final_step.start()
                if not await repo.advance_step(final_step, ReviewStepStatus.PENDING.value):
                    raise RunConcurrentModificationError(run_id)
            if final_step.status is not ReviewStepStatus.RUNNING:
                raise IdempotencyConflictError(final_step.idempotency_key)
            if not await run_repo.update_status(
                run_id,
                RunStatus.RUNNING,
                RunStatus.SUCCEEDED,
                run.event_sequence + 1,
            ):
                raise RunConcurrentModificationError(run_id)
            if not await repo.advance_step(
                final_step.succeed({"final_artifact_id": final_artifact_id}),
                ReviewStepStatus.RUNNING.value,
            ):
                raise RunConcurrentModificationError(run_id)
            await self._event_repo_factory(session).add(
                create_event(
                    run_id=run_id,
                    sequence=run.event_sequence,
                    event_type="run_succeeded",
                    actor_type="system",
                    correlation_id=correlation_id,
                    payload={"final_artifact_id": final_artifact_id},
                )
            )
            await session.commit()
            notify = True
        if notify:
            await notify_run_event(self._event_notifier, run_id)

    async def _load_context(
        self,
        run_id,
        project_id,
        owner_id,
        outline_id,
        matrix_id,
        section_ids,
        claim_set_id,
        consistency_id,
    ):
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
                or review.current_stage not in {ReviewStage.EXPORT_REVIEW, ReviewStage.FINALIZE}
                or review.current_outline_output_id != outline_id
            ):
                raise RunNotFoundError(run_id)
            outputs = await repo.list_outputs_scoped(run_id, project_id, owner_id)
            by_id = {item.output_id: item for item in outputs}
            matrix = by_id.get(matrix_id)
            consistency = by_id.get(consistency_id)
            section_outputs = [by_id.get(item) for item in section_ids]
            if (
                matrix is None
                or matrix.output_type is not ReviewOutputType.EVIDENCE_MATRIX
                or consistency is None
                or consistency.output_type is not ReviewOutputType.CONSISTENCY_REPORT
                or any(
                    item is None or item.output_type is not ReviewOutputType.SECTION
                    for item in section_outputs
                )
            ):
                raise ReviewExportInvalidError("review_export_output_scope_invalid")
            steps = await repo.list_steps_scoped(run_id, project_id, owner_id)
            consistency_step = _step(steps, ReviewStepKey.CONSISTENCY_CHECK)
            if (
                consistency_step.status is not ReviewStepStatus.SUCCEEDED
                or consistency_step.output_refs != {"consistency_output_id": consistency_id}
                or consistency_step.input_refs
                != {
                    "outline_output_id": outline_id,
                    "evidence_matrix_output_id": matrix_id,
                    "section_output_ids": list(section_ids),
                    "claim_set_id": claim_set_id,
                    "prompt_version": "consistency_check.v1",
                    "schema_version": "consistency-report.v1",
                }
            ):
                raise ReviewExportInvalidError("review_export_consistency_step_invalid")
            claim_repo = self._claim_set_repo_factory(session)
            claim_set = await claim_repo.get_by_run_id(run_id)
            if claim_set is None or claim_set.claim_set_id != claim_set_id:
                raise ReviewExportInvalidError("review_export_claim_set_invalid")
            claims = await claim_repo.list_claims(claim_set_id)
            citations = [
                item for claim in claims for item in await claim_repo.list_citations(claim.claim_id)
            ]
            evidence = await self._evidence_repo_factory(session).list_by_run(run_id)
            evidence_ids = {item.evidence_id for item in evidence}
            sections = []
            for raw in section_outputs:
                assert raw is not None
                parsed = parse_section_draft_json(json.dumps(raw.payload, ensure_ascii=False))
                sections.append(
                    validate_section_draft(
                        parsed,
                        expected_section_key=str(raw.payload.get("section_key", "")),
                        expected_title=str(raw.payload.get("title", "")),
                        allowed_evidence_ids=evidence_ids,
                    )
                )
            strategy = next(
                (
                    item
                    for item in outputs
                    if item.output_type is ReviewOutputType.SEARCH_STRATEGY
                    and item.schema_version == "search-strategy.v1"
                ),
                None,
            )
            if strategy is None:
                raise ReviewExportInvalidError("review_export_strategy_missing")
            validate_search_strategy(
                parse_search_strategy(json.dumps(strategy.payload, ensure_ascii=False))
            )
            return {
                "review": review,
                "strategy": strategy,
                "matrix": matrix,
                "consistency": consistency,
                "sections": sections,
                "claims": claims,
                "citations": citations,
                "evidence": evidence,
                "sources": await repo.list_sources_scoped(run_id, project_id, owner_id),
                "outputs": outputs,
                "model_invocations": await self._model_invocation_repo_factory(
                    session
                ).list_by_run(run_id),
            }

    @staticmethod
    def _statistics(context) -> dict[str, int]:
        sources = context["sources"]
        invocations = context["model_invocations"]
        return {
            "source_discovered": len(sources),
            "source_ready": sum(item.status is ReviewSourceStatus.READY for item in sources),
            "source_failed": sum(item.status is ReviewSourceStatus.FAILED for item in sources),
            "model_invocations": len(invocations),
            "prompt_tokens": sum(item.prompt_tokens or 0 for item in invocations),
            "completion_tokens": sum(item.completion_tokens or 0 for item in invocations),
        }

    def _artifact_contents(self, context, markdown, reference_mapping, statistics):
        sources = [
            {
                "source_id": item.source_id,
                "rank": item.rank,
                "arxiv_id": item.arxiv_id,
                "arxiv_version": item.arxiv_version,
                "status": item.status.value,
                "paper_id": item.paper_id,
                "paper_version_id": item.paper_version_id,
                "failure_code": item.failure_code,
                "metadata": item.metadata_snapshot,
            }
            for item in context["sources"]
        ]
        review = context["review"]
        failed = [item for item in sources if item["status"] == "failed"]
        summary = {
            "workflow_version": review.workflow_version,
            "model_profile_version": review.model_profile_version,
            "prompt_versions": review.prompt_versions,
            "config_snapshot": review.config_snapshot,
            "statistics": statistics,
            "source_counts": {
                "total": len(sources),
                "ready": sum(item["status"] == "ready" for item in sources),
                "failed": len(failed),
            },
            "failed_sources": [
                {"source_id": item["source_id"], "failure_code": item["failure_code"]}
                for item in failed
            ],
            "known_limitations": [
                "一致性报告只检查结构化章节摘要、Claim 与术语，不是通用事实 Judge。",
                "自动化校验保证引用闭包，不保证模型 Claim 与 Evidence 的完全语义蕴含。",
            ],
        }
        return {
            ArtifactType.REVIEW_MARKDOWN: markdown.encode(),
            ArtifactType.SEARCH_STRATEGY: _json_bytes(context["strategy"].payload),
            ArtifactType.SOURCE_MANIFEST: _json_bytes({"sources": sources}),
            ArtifactType.EVIDENCE_MATRIX: _json_bytes(context["matrix"].payload),
            ArtifactType.BIBLIOGRAPHY: _json_bytes({"references": list(reference_mapping)}),
            ArtifactType.RUN_SUMMARY: _json_bytes(summary),
        }

    async def _commit_export(
        self, proposed_output, manifest, *, project_id, owner_id, correlation_id
    ):
        created_event = False
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(proposed_output.review_run_id, owner_id)
            if (
                run is None
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
            ):
                raise RunNotFoundError(proposed_output.review_run_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped_for_update(run.run_id, project_id, owner_id)
            if review is None or review.current_stage not in {
                ReviewStage.EXPORT_REVIEW,
                ReviewStage.FINALIZE,
            }:
                raise RunNotFoundError(run.run_id)
            step = await repo.get_or_add_step(
                create_run_step(
                    run_id=run.run_id,
                    step_key=ReviewStepKey.EXPORT_REVIEW,
                    sequence=13,
                    idempotency_key=f"{run.run_id}:export-review:{EXPORT_VERSION}",
                    input_refs={
                        "outline_output_id": proposed_output.payload["approved_outline_output_id"],
                        "evidence_matrix_output_id": proposed_output.payload[
                            "evidence_matrix_output_id"
                        ],
                        "section_output_ids": proposed_output.payload["section_output_ids"],
                        "claim_set_id": proposed_output.payload["claim_set_id"],
                        "consistency_output_id": proposed_output.payload["consistency_output_id"],
                        "export_version": EXPORT_VERSION,
                    },
                )
            )
            if step.status is ReviewStepStatus.SUCCEEDED:
                if review.current_stage is not ReviewStage.FINALIZE:
                    raise IdempotencyConflictError(step.idempotency_key)
                output = next(
                    item
                    for item in await repo.list_outputs_scoped(run.run_id, project_id, owner_id)
                    if item.output_id == step.output_refs.get("final_output_id")
                )
                artifacts = tuple(
                    await repo.list_artifacts_scoped(run.run_id, project_id, owner_id)
                )
                return output, False, artifacts
            if step.status is ReviewStepStatus.PENDING:
                if review.current_stage is not ReviewStage.EXPORT_REVIEW:
                    raise IdempotencyConflictError(step.idempotency_key)
                running = step.start()
                if not await repo.advance_step(running, ReviewStepStatus.PENDING.value):
                    raise RunConcurrentModificationError(run.run_id)
                step = running
            if step.status is not ReviewStepStatus.RUNNING:
                raise IdempotencyConflictError(step.idempotency_key)
            output = await repo.get_or_add_output(proposed_output)
            if (
                output.output_type is not proposed_output.output_type
                or output.output_key != proposed_output.output_key
                or output.version != proposed_output.version
                or output.schema_version != proposed_output.schema_version
                or output.payload != proposed_output.payload
                or output.idempotency_key != proposed_output.idempotency_key
            ):
                raise IdempotencyConflictError(proposed_output.idempotency_key)
            artifacts = []
            for item in manifest:
                artifact_type = ArtifactType(item["artifact_type"])
                proposed = create_artifact(
                    review_run_id=run.run_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    artifact_type=artifact_type,
                    storage_key=item["storage_key"],
                    content_hash=item["content_hash"],
                    size_bytes=item["size_bytes"],
                    media_type=item["media_type"],
                    idempotency_key=f"{run.run_id}:artifact:{artifact_type.value}:{EXPORT_VERSION}",
                    source_output_id=output.output_id,
                    metadata={"export_version": EXPORT_VERSION},
                )
                persisted = await repo.get_or_add_artifact(proposed)
                if (
                    replace(
                        persisted, artifact_id=proposed.artifact_id, created_at=proposed.created_at
                    )
                    != proposed
                ):
                    raise IdempotencyConflictError(proposed.idempotency_key)
                artifacts.append(persisted)
            markdown = next(
                item for item in artifacts if item.artifact_type is ArtifactType.REVIEW_MARKDOWN
            )
            updated = replace(
                review,
                current_stage=ReviewStage.FINALIZE,
                final_artifact_id=markdown.artifact_id,
                statistics_summary=proposed_output.payload["statistics"],
                updated_at=datetime.now(UTC),
            )
            if not await repo.advance_review_final(
                updated,
                expected_stage=ReviewStage.EXPORT_REVIEW.value,
                expected_final_artifact_id=review.final_artifact_id,
            ):
                raise RunConcurrentModificationError(run.run_id)
            completed = step.succeed(
                {
                    "final_output_id": output.output_id,
                    "artifact_ids": [item.artifact_id for item in artifacts],
                    "final_artifact_id": markdown.artifact_id,
                }
            )
            if not await repo.advance_step(completed, ReviewStepStatus.RUNNING.value):
                raise RunConcurrentModificationError(run.run_id)
            await self._event_repo_factory(session).add(
                create_event(
                    run_id=run.run_id,
                    sequence=run.event_sequence,
                    event_type="review_artifact_created",
                    actor_type="system",
                    correlation_id=correlation_id,
                    payload={
                        "final_artifact_id": markdown.artifact_id,
                        "artifact_count": len(artifacts),
                    },
                )
            )
            if not await run_repo.update_status(
                run.run_id,
                RunStatus.RUNNING,
                RunStatus.RUNNING,
                run.event_sequence + 1,
            ):
                raise RunConcurrentModificationError(run.run_id)
            await session.commit()
            created_event = True
        return output, created_event, tuple(artifacts)


def _step(steps, key):
    result = next((item for item in steps if item.step_key is key), None)
    if result is None:
        raise ReviewExportInvalidError(f"{key.value}_step_missing")
    return result


def _json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
