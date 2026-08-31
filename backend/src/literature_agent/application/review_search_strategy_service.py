"""固定 Review 搜索策略的模型生成、校验与幂等持久化。"""

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    IdempotencyConflictError,
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.model_types import ChatMessage, ChatResult
from literature_agent.domain.review import (
    ReviewOutput,
    ReviewOutputType,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    create_review_output,
    create_run_step,
)
from literature_agent.domain.review_search_strategy import (
    SEARCH_STRATEGY_JSON_SCHEMA,
    SearchStrategyValidationError,
    parse_search_strategy,
    validate_search_strategy,
)
from literature_agent.domain.run import RunStatus, RunType

TSession = TypeVar("TSession", bound=Session)
PROMPT_VERSION = "search_strategy.v1"
SCHEMA_VERSION = "search-strategy.v1"
SEARCH_STRATEGY_MAX_OUTPUT_TOKENS = 8_000
SUPPORTED_MODEL_PROFILE_VERSIONS = frozenset(
    {"review-default.v1", "review-default.v2", "review-default.v3"}
)


class ReviewSearchStrategyModel(Protocol):
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
    ) -> ChatResult: ...


@dataclass(frozen=True, slots=True)
class SearchStrategyResult:
    output: ReviewOutput
    model_invocations: int


class ReviewSearchStrategyService[TSession: Session]:
    """为固定 Review Workflow 生成可审计的检索与分析策略。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        model_gateway: ReviewSearchStrategyModel,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._event_repo_factory = event_repo_factory
        self._model = model_gateway
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def formulate(
        self, *, run_id: str, project_id: str, owner_id: str, correlation_id: str
    ) -> SearchStrategyResult:
        existing, question = await self._load(run_id, project_id, owner_id)
        if existing is not None:
            self._validate_output(existing, run_id)
            return SearchStrategyResult(existing, 0)
        result = await self._model.generate(
            [
                ChatMessage(
                    "system",
                    "你负责为固定文献综述生成受限 arXiv 检索式和 3–6 个分析维度。只返回 JSON。",
                ),
                ChatMessage(
                    "user",
                    json.dumps(
                        {
                            "research_question": question,
                            "requirements": {
                                "arxiv_query": "只使用 arXiv 允许字段，不得包含 URL",
                                "dimension_key": "snake_case",
                                "dimension_count": "3-6",
                            },
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            json_schema=SEARCH_STRATEGY_JSON_SCHEMA,
            max_tokens=SEARCH_STRATEGY_MAX_OUTPUT_TOKENS,
            run_id=run_id,
        )
        try:
            strategy = validate_search_strategy(parse_search_strategy(result.content))
        except SearchStrategyValidationError:
            await self._fail_step(run_id, project_id, owner_id)
            raise
        output = create_review_output(
            review_run_id=run_id,
            output_type=ReviewOutputType.SEARCH_STRATEGY,
            output_key="search-strategy",
            version=1,
            schema_version=SCHEMA_VERSION,
            payload=strategy.to_payload(),
            idempotency_key=f"{run_id}:search-strategy:{PROMPT_VERSION}",
        )
        persisted, emitted = await self._persist(output, project_id, owner_id, correlation_id)
        if emitted:
            await notify_run_event(self._event_notifier, run_id)
        return SearchStrategyResult(persisted, 1)

    async def _load(self, run_id, project_id, owner_id):
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped(run_id, project_id, owner_id)
            if (
                run is None
                or review is None
                or run.project_id != project_id
                or run.owner_id != owner_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
                or review.workflow_version not in {"review.v1", "review.v2"}
                or review.model_profile_version not in SUPPORTED_MODEL_PROFILE_VERSIONS
                or review.prompt_versions.get("search_strategy") != PROMPT_VERSION
            ):
                raise RunNotFoundError(run_id)
            outputs = await repo.list_outputs_scoped(run_id, project_id, owner_id)
            existing = next(
                (item for item in outputs if item.output_type is ReviewOutputType.SEARCH_STRATEGY),
                None,
            )
            return existing, review.research_question

    async def _persist(self, output, project_id, owner_id, correlation_id):
        emitted = False
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run = await run_repo.get_by_id_for_update(output.review_run_id, owner_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped_for_update(
                output.review_run_id, project_id, owner_id
            )
            if (
                run is None
                or review is None
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
                or run.status is not RunStatus.RUNNING
            ):
                raise RunNotFoundError(output.review_run_id)
            step = await repo.get_or_add_step(
                create_run_step(
                    run_id=run.run_id,
                    step_key=ReviewStepKey.FORMULATE_SEARCH_STRATEGY,
                    sequence=2,
                    idempotency_key=f"{run.run_id}:formulate-search-strategy:{PROMPT_VERSION}",
                    input_refs={"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION},
                )
            )
            if step.status is ReviewStepStatus.PENDING:
                running = step.start()
                if not await repo.advance_step(running, ReviewStepStatus.PENDING.value):
                    raise RunConcurrentModificationError(run.run_id)
                step = running
            persisted = await repo.get_or_add_output(output)
            self._validate_output(persisted, run.run_id)
            refs = {"search_strategy_output_id": persisted.output_id}
            if step.status is ReviewStepStatus.RUNNING:
                if not await repo.advance_step(step.succeed(refs), ReviewStepStatus.RUNNING.value):
                    raise RunConcurrentModificationError(run.run_id)
                if not await run_repo.update_status(
                    run.run_id, RunStatus.RUNNING, RunStatus.RUNNING, run.event_sequence + 1
                ):
                    raise RunConcurrentModificationError(run.run_id)
                await self._event_repo_factory(session).add(
                    create_event(
                        run_id=run.run_id,
                        sequence=run.event_sequence,
                        event_type="search_strategy_completed",
                        actor_type="system",
                        correlation_id=correlation_id,
                        payload={
                            "output_id": persisted.output_id,
                            "dimension_count": len(persisted.payload["dimensions"]),
                        },
                    )
                )
                if review.current_stage in {
                    ReviewStage.VALIDATE_REQUEST,
                    ReviewStage.FORMULATE_SEARCH_STRATEGY,
                }:
                    advanced = replace(
                        review,
                        current_stage=ReviewStage.SEARCH_ARXIV,
                        updated_at=datetime.now(UTC),
                    )
                    if not await repo.advance_review_stage(
                        advanced,
                        expected_stage=review.current_stage.value,
                    ):
                        raise RunConcurrentModificationError(run.run_id)
                emitted = True
            elif step.status is not ReviewStepStatus.SUCCEEDED or step.output_refs != refs:
                raise IdempotencyConflictError(step.idempotency_key)
            await session.commit()
        return persisted, emitted

    async def _fail_step(self, run_id, project_id, owner_id):
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id_for_update(run_id, owner_id)
            repo = self._review_repo_factory(session)
            if (
                run is None
                or run.project_id != project_id
                or run.status is not RunStatus.RUNNING
                or await repo.get_review_run_scoped_for_update(run_id, project_id, owner_id) is None
            ):
                raise RunNotFoundError(run_id)
            step = await repo.get_or_add_step(
                create_run_step(
                    run_id=run_id,
                    step_key=ReviewStepKey.FORMULATE_SEARCH_STRATEGY,
                    sequence=2,
                    idempotency_key=f"{run_id}:formulate-search-strategy:{PROMPT_VERSION}",
                    input_refs={"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION},
                )
            )
            if step.status in {
                ReviewStepStatus.PENDING,
                ReviewStepStatus.RUNNING,
            } and not await repo.advance_step(
                step.fail("search_strategy_invalid"), step.status.value
            ):
                raise RunConcurrentModificationError(run_id)
            await session.commit()

    @staticmethod
    def _validate_output(output, run_id):
        key = f"{run_id}:search-strategy:{PROMPT_VERSION}"
        if (
            output.review_run_id != run_id
            or output.output_type is not ReviewOutputType.SEARCH_STRATEGY
            or output.output_key != "search-strategy"
            or output.version != 1
            or output.schema_version != SCHEMA_VERSION
            or output.idempotency_key != key
        ):
            raise IdempotencyConflictError(key)
        validate_search_strategy(
            parse_search_strategy(json.dumps(output.payload, ensure_ascii=False))
        )
