"""固定综述 Workflow 的创建应用服务。"""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRepository,
)
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    IdempotencyConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
    RunNotFoundError,
)
from literature_agent.domain.queue_outbox import create_outbox_entry
from literature_agent.domain.review import (
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    create_review_run,
    create_run_step,
)
from literature_agent.domain.run import RunType, create_run

TSession = TypeVar("TSession", bound=Session)

WORKFLOW_VERSION = "review.v1"
MODEL_PROFILE_VERSION = "review-default.v2"
PROMPT_VERSIONS = {
    "search_strategy": "search_strategy.v1",
    "evidence_extract": "review-evidence-extraction.v1",
    "outline_generate": "outline_generate.v1",
    "section_draft": "section_draft.v1",
    "consistency_check": "consistency_check.v1",
}
DEFAULT_CONFIG_SNAPSHOT = {
    "source_limit": 3,
    "minimum_ready_papers": 1,
    "full_text_token_threshold": 12_000,
    "retrieval_top_k_per_dimension": 5,
    "evidence_context_token_limit": 16_000,
    "section_output_token_limit": 8_000,
    "consistency_output_token_limit": 2_000,
}
_IDEMPOTENCY_KEY_MAX_LENGTH = 255


@dataclass(frozen=True, slots=True)
class CreateReviewRunResult:
    """创建 Review Run 的应用结果。"""

    run_id: str
    status: str
    reused: bool = False


def _request_hash(project_id: str, research_question: str) -> str:
    """对影响创建语义的输入生成稳定指纹。"""
    payload = json.dumps(
        {
            "project_id": project_id,
            "research_question": research_question,
            "workflow_version": WORKFLOW_VERSION,
            "model_profile_version": MODEL_PROFILE_VERSION,
            "prompt_versions": PROMPT_VERSIONS,
            "config_snapshot": DEFAULT_CONFIG_SNAPSHOT,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ReviewWorkflowService:
    """组织 Project-scoped Review Run 的原子创建。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        idempotency_repo_factory: Callable[[TSession], IdempotencyRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._idempotency_repo_factory = idempotency_repo_factory
        self._review_repo_factory = review_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def create_review_run(
        self,
        actor: ActorContext,
        project_id: str,
        research_question: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> CreateReviewRunResult:
        """原子创建通用 Run、Review 扩展、首个 Event 与 Outbox。

        当前切片复用通用 ``idempotency_keys``。HTTP Route 尚未接入，后续
        API 只需把强制的 ``Idempotency-Key`` 传入本服务。
        """
        question = research_question.strip()
        if not idempotency_key or len(idempotency_key) > _IDEMPOTENCY_KEY_MAX_LENGTH:
            raise ValueError("Idempotency-Key 不能为空且长度不得超过 255")
        fingerprint = _request_hash(project_id, question)

        async with self._session_factory() as session:
            idempotency_repo = self._idempotency_repo_factory(session)
            existing = await idempotency_repo.get(actor.owner_id, idempotency_key)
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise IdempotencyConflictError(idempotency_key)
                if existing.run_id is None:
                    raise RunNotFoundError("missing-idempotency-run")
                run = await self._run_repo_factory(session).get_by_id(existing.run_id)
                if run is None or run.owner_id != actor.owner_id:
                    raise RunNotFoundError(existing.run_id)
                return CreateReviewRunResult(
                    run_id=run.run_id,
                    status=run.status.value,
                    reused=True,
                )

            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            if project.is_archived:
                raise ProjectArchivedError(project_id)

            run = create_run(
                project_id=project_id,
                owner_id=actor.owner_id,
                run_type=RunType.REVIEW,
                input_payload={
                    "research_question": question,
                    "workflow_version": WORKFLOW_VERSION,
                    "model_profile_version": MODEL_PROFILE_VERSION,
                },
            )
            review_run = create_review_run(
                run_id=run.run_id,
                research_question=question,
                workflow_version=WORKFLOW_VERSION,
                model_profile_version=MODEL_PROFILE_VERSION,
                prompt_versions=PROMPT_VERSIONS,
                config_snapshot=DEFAULT_CONFIG_SNAPSHOT,
            )
            review_run = replace(
                review_run,
                current_stage=ReviewStage.FORMULATE_SEARCH_STRATEGY,
                updated_at=review_run.created_at,
            )
            validate_step = create_run_step(
                run_id=run.run_id,
                step_key=ReviewStepKey.VALIDATE_REQUEST,
                sequence=1,
                idempotency_key=f"{run.run_id}:validate-request:review.v1",
                input_refs={"workflow_version": WORKFLOW_VERSION},
            )
            validate_step = replace(
                validate_step,
                status=ReviewStepStatus.SUCCEEDED,
                output_refs={"validated": True},
                started_at=validate_step.created_at,
                completed_at=validate_step.created_at,
            )
            created_event = create_event(
                run_id=run.run_id,
                sequence=1,
                event_type="review_run_created",
                actor_type="user",
                correlation_id=correlation_id,
                payload={
                    "status": run.status.value,
                    "workflow_version": WORKFLOW_VERSION,
                    "current_stage": review_run.current_stage.value,
                },
            )
            persisted_run = replace(run, event_sequence=2)

            run_repo = self._run_repo_factory(session)
            review_repo = self._review_repo_factory(session)
            await run_repo.add(persisted_run)
            await session.flush()
            await review_repo.add_review_run(review_run)
            await review_repo.add_step(validate_step)
            await self._event_repo_factory(session).add(created_event)
            await self._outbox_repo_factory(session).add(create_outbox_entry(run.run_id))
            await idempotency_repo.add(
                IdempotencyRecord(
                    owner_id=actor.owner_id,
                    idempotency_key=idempotency_key,
                    project_id=project_id,
                    request_hash=fingerprint,
                    run_id=run.run_id,
                    status=run.status.value,
                )
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        await notify_run_event(self._event_notifier, run.run_id)
        return CreateReviewRunResult(run_id=run.run_id, status=run.status.value)
