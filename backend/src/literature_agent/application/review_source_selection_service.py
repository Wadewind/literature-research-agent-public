"""Review v2 的 arXiv 候选人工筛选边界。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
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
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.review import (
    HumanInput,
    HumanInputAction,
    HumanInputRequest,
    HumanInputRequestKind,
    HumanInputRequestStatus,
    ReviewOutput,
    ReviewOutputType,
    ReviewSourceKind,
    ReviewSourceStatus,
    ReviewStage,
    create_human_input,
    create_human_input_request,
    create_review_output,
)
from literature_agent.domain.run import RunStatus, RunType

SOURCE_CANDIDATES_SCHEMA_VERSION = "source-candidates.v1"


@dataclass(frozen=True, slots=True)
class SourceSelectionSubmitResult:
    """来源筛选提交结果。"""

    human_input: HumanInput
    selected_source_ids: tuple[str, ...]
    replayed: bool = False


class ReviewSourceSelectionService[TSession: Session]:
    """持久化候选快照，并在下载前暂停等待人工筛选。"""

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

    async def pause(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        correlation_id: str,
    ) -> tuple[ReviewOutput, HumanInputRequest]:
        """固化候选 Output，并把运行态推进到 WAITING_INPUT。"""
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            repo = self._review_repo_factory(session)
            run = await run_repo.get_by_id_for_update(run_id, owner_id)
            review = await repo.get_review_run_scoped_for_update(run_id, project_id, owner_id)
            if (
                run is None
                or review is None
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
            ):
                raise RunNotFoundError(run_id)
            sources = await repo.list_sources_scoped(run_id, project_id, owner_id)
            candidates = [
                item
                for item in sources
                if item.source_kind is ReviewSourceKind.ARXIV
                and item.status is ReviewSourceStatus.DISCOVERED
            ]
            ready_count = sum(item.status is ReviewSourceStatus.READY for item in sources)
            source_limit = int(review.config_snapshot.get("source_limit", 3))
            payload = {
                "candidates": [
                    {
                        "source_id": item.source_id,
                        "arxiv_id": item.arxiv_id,
                        "arxiv_version": item.arxiv_version,
                        "title": item.metadata_snapshot.get("title"),
                        "abstract": item.metadata_snapshot.get("abstract"),
                        "authors": item.metadata_snapshot.get("authors", []),
                        "published_at": item.metadata_snapshot.get("published_at"),
                        "page_count": item.metadata_snapshot.get("page_count"),
                        "pdf_url": item.metadata_snapshot.get("pdf_url"),
                    }
                    for item in candidates
                ],
                "ready_project_source_count": ready_count,
                "max_selected": max(0, source_limit - ready_count),
                "source_limit": source_limit,
            }
            proposed_output = create_review_output(
                review_run_id=run_id,
                output_type=ReviewOutputType.SOURCE_CANDIDATES,
                output_key="source-candidates",
                version=1,
                schema_version=SOURCE_CANDIDATES_SCHEMA_VERSION,
                payload=payload,
                idempotency_key=f"{run_id}:source-candidates:v1",
            )
            output = await repo.get_or_add_output(proposed_output)
            if (
                output.payload != payload
                or output.schema_version != SOURCE_CANDIDATES_SCHEMA_VERSION
            ):
                raise IdempotencyConflictError(proposed_output.idempotency_key)

            if run.status is RunStatus.WAITING_INPUT:
                request = await repo.get_open_human_input_request_scoped(
                    run_id, project_id, owner_id
                )
                if (
                    request is None
                    or request.request_kind is not HumanInputRequestKind.SOURCE_SELECTION
                    or request.outline_output_id != output.output_id
                ):
                    raise HumanInputConflictError("source_selection_request_conflict")
                return output, request
            if run.status is not RunStatus.RUNNING:
                raise RunConcurrentModificationError(run_id)

            proposed_request = create_human_input_request(
                review_run_id=run_id,
                request_version=1,
                outline_output_id=output.output_id,
                allowed_actions=[HumanInputAction.SELECT_SOURCES],
                request_kind=HumanInputRequestKind.SOURCE_SELECTION,
            )
            request = await repo.get_or_add_human_input_request(proposed_request)
            if (
                request.request_kind is not HumanInputRequestKind.SOURCE_SELECTION
                or request.outline_output_id != output.output_id
                or request.allowed_actions != (HumanInputAction.SELECT_SOURCES,)
                or request.status is not HumanInputRequestStatus.OPEN
            ):
                raise IdempotencyConflictError(f"{run_id}:source-selection:1")
            if review.current_stage is not ReviewStage.IMPORT_ARXIV_PAPERS:
                raise RunConcurrentModificationError(run_id)
            if not await repo.advance_review_stage(
                replace(
                    review,
                    current_stage=ReviewStage.REVIEW_SOURCES,
                    updated_at=datetime.now(UTC),
                ),
                expected_stage=ReviewStage.IMPORT_ARXIV_PAPERS.value,
            ):
                raise RunConcurrentModificationError(run_id)
            if not await run_repo.update_status(
                run_id,
                RunStatus.RUNNING,
                RunStatus.WAITING_INPUT,
                run.event_sequence + 1,
            ):
                raise RunConcurrentModificationError(run_id)
            await self._event_repo_factory(session).add(
                create_event(
                    run_id=run_id,
                    sequence=run.event_sequence,
                    event_type="review_source_selection_requested",
                    actor_type="system",
                    correlation_id=correlation_id,
                    payload={
                        "request_id": request.request_id,
                        "candidate_output_id": output.output_id,
                        "candidate_count": len(candidates),
                        "max_selected": payload["max_selected"],
                    },
                )
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run_id)
        return output, request

    async def submit(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        request_id: str,
        request_version: int,
        candidate_output_id: str,
        selected_source_ids: list[str],
        idempotency_key: str,
        correlation_id: str,
    ) -> SourceSelectionSubmitResult:
        """接受来源选择、拒绝未选候选，并原子恢复父 Run。"""
        selected = tuple(dict.fromkeys(selected_source_ids))
        if len(selected) != len(selected_source_ids):
            raise HumanInputConflictError("source_selection_contains_duplicates")
        if not idempotency_key or len(idempotency_key) > 255:
            raise HumanInputConflictError("human_input_idempotency_key_invalid")
        async with self._session_factory() as session:
            repo = self._review_repo_factory(session)
            replay = await repo.get_human_input_by_idempotency_scoped(
                owner_id, idempotency_key, run_id, project_id, owner_id
            )
            if replay is not None:
                replay_request = await repo.get_human_input_request_scoped_for_update(
                    replay.request_id, run_id, project_id, owner_id
                )
                if (
                    replay_request is None
                    or replay_request.request_kind
                    is not HumanInputRequestKind.SOURCE_SELECTION
                    or replay_request.outline_output_id != candidate_output_id
                    or replay_request.status is not HumanInputRequestStatus.RESOLVED
                    or replay_request.resolved_input_id != replay.human_input_id
                    or replay.request_id != request_id
                    or replay.request_version != request_version
                    or replay.action is not HumanInputAction.SELECT_SOURCES
                    or tuple(replay.payload.get("selected_source_ids", ())) != selected
                ):
                    raise HumanInputConflictError("human_input_idempotency_conflict")
                return SourceSelectionSubmitResult(replay, selected, True)
            request = await repo.get_human_input_request_scoped_for_update(
                request_id, run_id, project_id, owner_id
            )
            if (
                request is None
                or request.status is not HumanInputRequestStatus.OPEN
                or request.request_kind is not HumanInputRequestKind.SOURCE_SELECTION
                or request.request_version != request_version
                or request.outline_output_id != candidate_output_id
            ):
                raise HumanInputConflictError("source_selection_request_stale")
            outputs = await repo.list_outputs_scoped(run_id, project_id, owner_id)
            output = next(
                (
                    item
                    for item in outputs
                    if item.output_id == candidate_output_id
                    and item.output_type is ReviewOutputType.SOURCE_CANDIDATES
                    and item.schema_version == SOURCE_CANDIDATES_SCHEMA_VERSION
                ),
                None,
            )
            if output is None:
                raise HumanInputConflictError("source_candidates_output_missing")
            sources = await repo.list_sources_scoped(run_id, project_id, owner_id)
            candidates = {
                item.source_id: item
                for item in sources
                if item.source_kind is ReviewSourceKind.ARXIV
                and item.status is ReviewSourceStatus.DISCOVERED
            }
            if not set(selected).issubset(candidates):
                raise HumanInputConflictError("source_selection_out_of_scope")
            ready_count = sum(item.status is ReviewSourceStatus.READY for item in sources)
            review = await repo.get_review_run_scoped_for_update(run_id, project_id, owner_id)
            if review is None or review.current_stage is not ReviewStage.REVIEW_SOURCES:
                raise HumanInputConflictError("source_selection_stage_stale")
            source_limit = int(review.config_snapshot.get("source_limit", 3))
            if ready_count + len(selected) > source_limit:
                raise HumanInputConflictError("source_selection_limit_exceeded")
            if ready_count + len(selected) < 1:
                raise HumanInputConflictError("review_requires_at_least_one_source")
            for source_id, source in candidates.items():
                if source_id not in selected:
                    await repo.save_source(source.reject())
            proposed_input = create_human_input(
                request=request,
                action=HumanInputAction.SELECT_SOURCES,
                payload={"selected_source_ids": list(selected)},
                submitted_by=owner_id,
                idempotency_key=idempotency_key,
            )
            human_input = await repo.get_or_add_human_input(proposed_input)
            if human_input.payload != proposed_input.payload:
                raise HumanInputConflictError("human_input_idempotency_conflict")
            if not await repo.resolve_human_input_request(
                request.resolve(human_input.human_input_id),
                expected_status=HumanInputRequestStatus.OPEN.value,
            ):
                raise HumanInputConflictError("human_input_request_resolved")
            if not await repo.advance_review_stage(
                replace(
                    review,
                    current_stage=ReviewStage.IMPORT_ARXIV_PAPERS,
                    updated_at=datetime.now(UTC),
                ),
                expected_stage=ReviewStage.REVIEW_SOURCES.value,
            ):
                raise RunConcurrentModificationError(run_id)
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
                    "selected_source_ids": list(selected),
                },
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run_id)
        return SourceSelectionSubmitResult(human_input, selected)
