"""生产 Review Run 的分阶段执行器与 LangGraph 恢复编排。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime
from typing import TypeVar

from literature_agent.application.arxiv_import_service import ArxivProjectImportService
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.review_dependency_service import ReviewDependencyWaitService
from literature_agent.application.review_evidence_matrix_service import ReviewEvidenceMatrixService
from literature_agent.application.review_export_service import ReviewExportService
from literature_agent.application.review_outline_service import (
    ReviewOutlineDecisionService,
    ReviewOutlineService,
)
from literature_agent.application.review_search_strategy_service import (
    ReviewSearchStrategyService,
)
from literature_agent.application.review_section_service import ReviewSectionService
from literature_agent.application.review_source_selection_service import (
    ReviewSourceSelectionService,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.arxiv import ArxivSearchQuery
from literature_agent.domain.exceptions import NoReviewablePapersError, RunNotFoundError
from literature_agent.domain.review import ReviewSourceStatus, ReviewStage
from literature_agent.domain.run import Run, RunStatus, RunType
from literature_agent.infrastructure.workflow.postgres_checkpoint import PostgresCheckpointStore
from literature_agent.metrics import metrics, observe_review_stage
from literature_agent.workflows.review_export_nodes import ReviewExportGraphNodes
from literature_agent.workflows.review_graph import (
    ReviewGraphFactory,
    ReviewGraphState,
    ReviewWorkflowRuntime,
)
from literature_agent.workflows.review_outline_nodes import ReviewOutlineGraphNodes
from literature_agent.workflows.review_section_nodes import ReviewSectionGraphNodes

TSession = TypeVar("TSession", bound=Session)


class ReviewExecutor[TSession: Session]:
    """按持久业务事实恢复 Review；只有 Outline 使用 LangGraph Interrupt。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        strategy_service: ReviewSearchStrategyService,
        arxiv_service: ArxivProjectImportService,
        dependency_wait_service: ReviewDependencyWaitService,
        matrix_service: ReviewEvidenceMatrixService,
        outline_service: ReviewOutlineService,
        outline_decision_service: ReviewOutlineDecisionService,
        section_service: ReviewSectionService,
        export_service: ReviewExportService,
        source_selection_service: ReviewSourceSelectionService,
        checkpoint_store: PostgresCheckpointStore,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._strategy = strategy_service
        self._arxiv = arxiv_service
        self._dependency_wait = dependency_wait_service
        self._matrix = matrix_service
        self._outline = outline_service
        self._outline_decision = outline_decision_service
        self._sections = section_service
        self._export = export_service
        self._source_selection = source_selection_service
        self._checkpoints = checkpoint_store

    async def execute(self, run: Run, correlation_id: str) -> None:
        if run.run_type != RunType.REVIEW.value or run.status is not RunStatus.RUNNING:
            metrics.record_review_stage(ReviewStage.VALIDATE_REQUEST, "failed")
            raise RunNotFoundError(run.run_id)
        metrics.record_review_stage(ReviewStage.VALIDATE_REQUEST, "succeeded")
        owner_id = run.owner_id
        project_id = run.project_id
        actor = ActorContext(owner_id)
        strategy = await observe_review_stage(
            ReviewStage.FORMULATE_SEARCH_STRATEGY,
            self._strategy.formulate(
                run_id=run.run_id,
                project_id=project_id,
                owner_id=owner_id,
                correlation_id=correlation_id,
            ),
        )
        snapshot = await self._review_snapshot(run.run_id, project_id, owner_id)
        sources = await self._sources(run.run_id, project_id, owner_id)
        if snapshot.workflow_version == "review.v1":
            query = ArxivSearchQuery(
                strategy.output.payload["arxiv_query"],
                max_results=int(snapshot.config_snapshot.get("source_limit", 10)),
            )
            await observe_review_stage(
                ReviewStage.SEARCH_ARXIV,
                self._arxiv.search_sources(
                    actor=actor,
                    project_id=project_id,
                    review_run_id=run.run_id,
                    query=query,
                    correlation_id=correlation_id,
                ),
            )
        elif snapshot.workflow_version == "review.v2":
            auto_search = bool(snapshot.config_snapshot.get("auto_search_candidates"))
            if auto_search and snapshot.current_stage is ReviewStage.SEARCH_ARXIV:
                query = ArxivSearchQuery(
                    strategy.output.payload["arxiv_query"],
                    max_results=int(snapshot.config_snapshot.get("candidate_limit", 10)),
                )
                await observe_review_stage(
                    ReviewStage.SEARCH_ARXIV,
                    self._arxiv.search_sources(
                        actor=actor,
                        project_id=project_id,
                        review_run_id=run.run_id,
                        query=query,
                        correlation_id=correlation_id,
                        rank_offset=len(sources),
                    ),
                )
                sources = await self._sources(run.run_id, project_id, owner_id)
                if any(item.status is ReviewSourceStatus.DISCOVERED for item in sources):
                    await self._source_selection.pause(
                        run_id=run.run_id,
                        project_id=project_id,
                        owner_id=owner_id,
                        correlation_id=correlation_id,
                    )
                    return
                if not any(item.status is ReviewSourceStatus.READY for item in sources):
                    raise NoReviewablePapersError
            elif not auto_search and snapshot.current_stage is ReviewStage.SEARCH_ARXIV:
                await self._advance_to_import(run.run_id, project_id, owner_id)
        else:
            raise RunNotFoundError(run.run_id)
        await observe_review_stage(
            ReviewStage.IMPORT_ARXIV_PAPERS,
            self._arxiv.import_sources(
                actor=actor,
                project_id=project_id,
                review_run_id=run.run_id,
                correlation_id=correlation_id,
            ),
        )
        sources = await self._sources(run.run_id, project_id, owner_id)
        if any(
            item.status in {ReviewSourceStatus.DISCOVERED, ReviewSourceStatus.IMPORTING}
            for item in sources
        ):
            await observe_review_stage(
                ReviewStage.WAIT_FOR_INGESTION,
                self._dependency_wait.pause(run.run_id, project_id, owner_id, correlation_id),
            )
            return
        if not any(item.status is ReviewSourceStatus.READY for item in sources):
            raise NoReviewablePapersError

        matrix = await observe_review_stage(
            ReviewStage.BUILD_EVIDENCE_MATRIX,
            self._matrix.build(
                run_id=run.run_id,
                project_id=project_id,
                owner_id=owner_id,
                search_strategy_output_id=strategy.output.output_id,
                correlation_id=correlation_id,
            ),
        )
        outline_nodes = ReviewOutlineGraphNodes(
            owner_id=owner_id,
            outline_service=self._outline,
            decision_service=self._outline_decision,
        )
        section_nodes = ReviewSectionGraphNodes(owner_id=owner_id, service=self._sections)
        export_nodes = ReviewExportGraphNodes(owner_id=owner_id, service=self._export)
        factory = ReviewGraphFactory(
            outline_entry_node=outline_nodes.propose,
            outline_decision_node=outline_nodes.apply_decision,
            section_draft_node=section_nodes.draft,
            section_validate_node=section_nodes.validate,
            consistency_node=section_nodes.consistency,
            export_node=export_nodes.export,
            finalize_node=export_nodes.finalize,
        )
        async with self._checkpoints.open() as checkpointer:
            runtime = ReviewWorkflowRuntime(factory, checkpointer)
            resolved = await self._latest_input(run.run_id, project_id, owner_id)
            current_review = await self._review_snapshot(run.run_id, project_id, owner_id)
            if resolved is not None and current_review.current_stage is ReviewStage.REVIEW_OUTLINE:
                request, human_input = resolved
                await runtime.resume_human_input(
                    run.run_id,
                    request_id=request.request_id,
                    human_input_id=human_input.human_input_id,
                )
                return
            initial = ReviewGraphState(
                review_run_id=run.run_id,
                project_id=project_id,
                workflow_version=snapshot.workflow_version,
                research_question=snapshot.research_question,
                search_strategy_output_id=strategy.output.output_id,
                review_source_ids=[item.source_id for item in sources],
                evidence_matrix_output_id=matrix.output.output_id,
                feedback_round=0,
            )
            if await runtime.has_checkpoint(run.run_id):
                await runtime.resume(run.run_id)
            else:
                await runtime.start(initial)

    async def _review_snapshot(self, run_id, project_id, owner_id):
        async with self._session_factory() as session:
            review = await self._review_repo_factory(session).get_review_run_scoped(
                run_id, project_id, owner_id
            )
            if review is None:
                raise RunNotFoundError(run_id)
            return review

    async def _sources(self, run_id, project_id, owner_id):
        async with self._session_factory() as session:
            return await self._review_repo_factory(session).list_sources_scoped(
                run_id, project_id, owner_id
            )

    async def _latest_input(self, run_id, project_id, owner_id):
        async with self._session_factory() as session:
            return await self._review_repo_factory(session).get_latest_resolved_human_input_scoped(
                run_id, project_id, owner_id
            )

    async def _advance_to_import(self, run_id, project_id, owner_id) -> None:
        async with self._session_factory() as session:
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped_for_update(
                run_id, project_id, owner_id
            )
            if review is None:
                raise RunNotFoundError(run_id)
            if review.current_stage is ReviewStage.IMPORT_ARXIV_PAPERS:
                return
            if review.current_stage is not ReviewStage.SEARCH_ARXIV:
                raise RunNotFoundError(run_id)
            advanced = replace(
                review,
                current_stage=ReviewStage.IMPORT_ARXIV_PAPERS,
                updated_at=datetime.now(UTC),
            )
            if not await repo.advance_review_stage(
                advanced, expected_stage=ReviewStage.SEARCH_ARXIV.value
            ):
                raise RunNotFoundError(run_id)
            await session.commit()
