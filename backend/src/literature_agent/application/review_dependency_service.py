"""Review Run 的论文依赖等待与确定性对账。"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import PaperVersionRepository
from literature_agent.application.ports.parse_revision_repository import ParseRevisionRepository
from literature_agent.application.ports.project_paper_repository import ProjectPaperRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.waiting_run_resume_service import (
    ResumeReason,
    WaitingRunResumeService,
)
from literature_agent.domain.chunk import ChunkSet
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    RunConcurrentModificationError,
    RunNotFoundError,
)
from literature_agent.domain.parse_revision import ParseRevisionStatus
from literature_agent.domain.review import (
    ReviewDependency,
    ReviewDependencyStatus,
    ReviewDependencyType,
    ReviewRun,
    ReviewSource,
    ReviewSourceStatus,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    create_review_dependency,
    create_run_step,
)
from literature_agent.domain.run import Run, RunStatus, RunType

logger = logging.getLogger(__name__)

_ACTIVE_CHILD_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.RETRY_WAIT,
    RunStatus.CANCEL_REQUESTED,
}


class ReviewDependencyWaitService[TSession: Session]:
    """把执行中的 Review Run 原子推进到论文依赖等待状态。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._review_repo_factory = review_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def pause(
        self,
        run_id: str,
        project_id: str,
        owner_id: str,
        correlation_id: str,
    ) -> Run:
        """提交 RUNNING→WAITING_DEPENDENCY 与开始等待 Event。

        Outbox 不在此事务中重置；当前 Worker 退出后由执行服务把 Attempt
        正常关闭为 PAUSED。
        """
        async with self._session_factory() as session:
            try:
                run_repo = self._run_repo_factory(session)
                run = await run_repo.get_by_id_for_update(run_id, owner_id)
                review_repo = self._review_repo_factory(session)
                review = await review_repo.get_review_run_scoped_for_update(
                    run_id, project_id, owner_id
                )
                if (
                    run is None
                    or run.project_id != project_id
                    or run.run_type != RunType.REVIEW.value
                    or review is None
                ):
                    raise RunNotFoundError(run_id)
                step = await review_repo.get_or_add_step(
                    create_run_step(
                        run_id=run_id,
                        step_key=ReviewStepKey.WAIT_FOR_INGESTION,
                        sequence=5,
                        idempotency_key=f"{run_id}:wait-for-ingestion:review.v1",
                    )
                )
                if step.status is ReviewStepStatus.PENDING:
                    running_step = step.start()
                    if not await review_repo.advance_step(
                        running_step, ReviewStepStatus.PENDING.value
                    ) or not await review_repo.advance_step(
                        running_step.pause({"waiting": True}),
                        ReviewStepStatus.RUNNING.value,
                    ):
                        raise RunConcurrentModificationError(run.run_id)
                elif step.status is not ReviewStepStatus.PAUSED:
                    raise RunConcurrentModificationError(run.run_id)
                if review.current_stage in {
                    ReviewStage.VALIDATE_REQUEST,
                    ReviewStage.SEARCH_ARXIV,
                    ReviewStage.IMPORT_ARXIV_PAPERS,
                }:
                    advanced_review = replace(
                        review,
                        current_stage=ReviewStage.WAIT_FOR_INGESTION,
                        updated_at=datetime.now(UTC),
                    )
                    if not await review_repo.advance_review_stage(
                        advanced_review, expected_stage=review.current_stage.value
                    ):
                        raise RunConcurrentModificationError(run.run_id)
                elif review.current_stage is not ReviewStage.WAIT_FOR_INGESTION:
                    raise RunConcurrentModificationError(run.run_id)
                waiting = run.transition_to(RunStatus.WAITING_DEPENDENCY)
                if not await run_repo.update_status(
                    run.run_id,
                    RunStatus.RUNNING,
                    RunStatus.WAITING_DEPENDENCY,
                    run.event_sequence + 1,
                ):
                    raise RunConcurrentModificationError(run.run_id)
                await self._event_repo_factory(session).add(
                    create_event(
                        run_id=run.run_id,
                        sequence=run.event_sequence,
                        event_type="dependency_wait_started",
                        actor_type="system",
                        correlation_id=correlation_id,
                        payload={},
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        await notify_run_event(self._event_notifier, run_id)
        return replace(waiting, event_sequence=waiting.event_sequence + 1)


class ReviewDependencyReconciler[TSession: Session]:
    """有界对账等待中的 Review Run，并固定可用论文集合。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        parse_revision_repo_factory: Callable[[TSession], ParseRevisionRepository],
        project_paper_repo_factory: Callable[[TSession], ProjectPaperRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        waiting_resume_service: WaitingRunResumeService[TSession],
        batch_size: int = 20,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("dependency_reconcile_batch_size_invalid")
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._review_repo_factory = review_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._parse_revision_repo_factory = parse_revision_repo_factory
        self._project_paper_repo_factory = project_paper_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._waiting_resume_service = waiting_resume_service
        self._batch_size = batch_size
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def reconcile_waiting(self) -> int:
        """处理一批候选，返回本轮恢复或终止的父 Run 数。"""
        async with self._session_factory() as session:
            candidates = await self._review_repo_factory(
                session
            ).list_dependency_reconcile_run_ids(self._batch_size)
        completed = 0
        for run_id in candidates:
            try:
                if await self.reconcile_run(run_id):
                    completed += 1
            except Exception:
                # 单个损坏/竞争候选不能阻塞同批次其他 Review Run；事务已由
                # reconcile_run 回滚，下一轮仍可重试并通过日志诊断。
                logger.exception("Review 依赖候选对账失败: run_id=%s", run_id)
        return completed

    async def reconcile_run(self, run_id: str) -> bool:
        """事务性对账单个父 Run；主要供批处理和故障注入测试复用。"""
        async with self._session_factory() as session:
            try:
                run_repo = self._run_repo_factory(session)
                visible = await run_repo.get_by_id(run_id)
                if visible is None:
                    return False
                run = await run_repo.get_by_id_for_update(run_id, visible.owner_id)
                if (
                    run is None
                    or run.status
                    not in {
                        RunStatus.WAITING_DEPENDENCY,
                        RunStatus.CANCEL_REQUESTED,
                        RunStatus.CANCELLED,
                    }
                    or run.run_type != RunType.REVIEW.value
                ):
                    await session.rollback()
                    return False
                cancelling = run.status in {
                    RunStatus.CANCEL_REQUESTED,
                    RunStatus.CANCELLED,
                }
                review_repo = self._review_repo_factory(session)
                review = await review_repo.get_review_run_scoped(
                    run.run_id, run.project_id, run.owner_id
                )
                if review is None:
                    raise RunNotFoundError(run.run_id)
                sources = await review_repo.list_sources_scoped(
                    run.run_id, run.project_id, run.owner_id
                )
                dependencies = await review_repo.list_dependencies_scoped(
                    run.run_id, run.project_id, run.owner_id
                )
                current = run
                for source in sources:
                    if source.status in {
                        ReviewSourceStatus.READY,
                        ReviewSourceStatus.FAILED,
                        ReviewSourceStatus.REJECTED,
                    }:
                        continue
                    if cancelling and source.status is ReviewSourceStatus.DISCOVERED:
                        await review_repo.save_source(source.reject())
                        continue
                    ready_chunk_set, failure_code = await self._evaluate_source(
                        session, current, source, dependencies
                    )
                    if ready_chunk_set is not None:
                        updated = source.mark_ready(
                            source.paper_id or "", source.paper_version_id or ""
                        )
                        await review_repo.save_source(updated)
                        dependencies = await self._mark_version_dependency(
                            review_repo,
                            dependencies,
                            source.paper_version_id or "",
                            failure_code=None,
                        )
                        if not any(
                            dep.dependency_type is ReviewDependencyType.CHUNK_SET
                            and dep.target_chunk_set_id == ready_chunk_set.chunk_set_id
                            for dep in dependencies
                        ):
                            dependency = create_review_dependency(
                                parent_run_id=run.run_id,
                                dependency_type=ReviewDependencyType.CHUNK_SET,
                                target_chunk_set_id=ready_chunk_set.chunk_set_id,
                            ).mark_satisfied()
                            await review_repo.add_dependency(dependency)
                            dependencies.append(dependency)
                        current = await self._append_event(
                            session,
                            current,
                            "review_source_ready",
                            {
                                "source_id": source.source_id,
                                "paper_id": source.paper_id,
                                "paper_version_id": source.paper_version_id,
                                "chunk_set_id": ready_chunk_set.chunk_set_id,
                            },
                        )
                    elif failure_code is not None:
                        await review_repo.save_source(source.mark_failed(failure_code))
                        dependencies = await self._mark_version_dependency(
                            review_repo,
                            dependencies,
                            source.paper_version_id or "",
                            failure_code=failure_code,
                        )
                        current = await self._append_event(
                            session,
                            current,
                            "review_source_failed",
                            {"source_id": source.source_id, "failure_code": failure_code},
                        )

                await self._advance_run_dependencies(
                    session, review_repo, dependencies, current
                )
                if cancelling:
                    if current.status is RunStatus.CANCEL_REQUESTED:
                        current.transition_to(RunStatus.CANCELLED)
                        if not await run_repo.update_status(
                            run.run_id,
                            RunStatus.CANCEL_REQUESTED,
                            RunStatus.CANCELLED,
                            current.event_sequence + 1,
                        ):
                            raise RunConcurrentModificationError(run.run_id)
                        await self._event_repo_factory(session).add(
                            create_event(
                                run_id=run.run_id,
                                sequence=current.event_sequence,
                                event_type="run_cancelled",
                                actor_type="system",
                                correlation_id=f"dependency-reconcile:{run.run_id}",
                                payload={},
                            )
                        )
                    await session.commit()
                    await notify_run_event(self._event_notifier, run_id)
                    return True
                sources = await review_repo.list_sources_scoped(
                    run.run_id, run.project_id, run.owner_id
                )
                terminal = all(
                    source.status
                    in {
                        ReviewSourceStatus.READY,
                        ReviewSourceStatus.FAILED,
                        ReviewSourceStatus.REJECTED,
                    }
                    for source in sources
                )
                if not terminal:
                    await session.commit()
                    # 来源状态 Event 已提交时也唤醒 SSE；返回计数仍只统计
                    # 已恢复或终止的父 Run。
                    if current.event_sequence != run.event_sequence:
                        await notify_run_event(self._event_notifier, run_id)
                    return False
                ready_count = sum(
                    source.status is ReviewSourceStatus.READY for source in sources
                )
                failed_count = sum(
                    source.status is ReviewSourceStatus.FAILED for source in sources
                )
                minimum = self._minimum_ready_papers(review)
                if ready_count >= minimum:
                    await self._complete_wait_step_and_advance_stage(
                        review_repo, review, ready_count, failed_count
                    )
                    await self._waiting_resume_service.resume_in_session(
                        session=session,
                        run_id=run.run_id,
                        project_id=run.project_id,
                        owner_id=run.owner_id,
                        reason=ResumeReason.DEPENDENCY_COMPLETED,
                        correlation_id=f"dependency-reconcile:{run.run_id}",
                        payload={
                            "ready_source_count": ready_count,
                            "failed_source_count": failed_count,
                        },
                    )
                else:
                    error_code = (
                        "no_reviewable_papers"
                        if ready_count == 0
                        else "insufficient_reviewable_papers"
                    )
                    current.transition_to(RunStatus.FAILED)
                    if not await run_repo.update_status(
                        run.run_id,
                        RunStatus.WAITING_DEPENDENCY,
                        RunStatus.FAILED,
                        current.event_sequence + 1,
                    ):
                        raise RunConcurrentModificationError(run.run_id)
                    await self._event_repo_factory(session).add(
                        create_event(
                            run_id=run.run_id,
                            sequence=current.event_sequence,
                            event_type="run_failed",
                            actor_type="system",
                            correlation_id=f"dependency-reconcile:{run.run_id}",
                            payload={
                                "error": {
                                    "type": error_code,
                                    "message": "没有足够的可用论文继续综述",
                                },
                                "ready_source_count": ready_count,
                                "minimum_ready_papers": minimum,
                            },
                        )
                    )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        await notify_run_event(self._event_notifier, run_id)
        return True

    async def _complete_wait_step_and_advance_stage(
        self,
        review_repo: ReviewRepository,
        review: ReviewRun,
        ready_count: int,
        failed_count: int,
    ) -> None:
        """在依赖恢复事务内完成等待 Step，并把下一阶段固定为 Matrix。"""
        step = await review_repo.get_or_add_step(
            create_run_step(
                run_id=review.run_id,
                step_key=ReviewStepKey.WAIT_FOR_INGESTION,
                sequence=5,
                idempotency_key=f"{review.run_id}:wait-for-ingestion:review.v1",
            )
        )
        if step.status is ReviewStepStatus.PENDING:
            running = step.start()
            if not await review_repo.advance_step(
                running, ReviewStepStatus.PENDING.value
            ):
                raise RunConcurrentModificationError(review.run_id)
            step = running.pause({"waiting": True})
            if not await review_repo.advance_step(
                step, ReviewStepStatus.RUNNING.value
            ):
                raise RunConcurrentModificationError(review.run_id)
        refs = {
            "ready_source_count": ready_count,
            "failed_source_count": failed_count,
        }
        if step.status is ReviewStepStatus.PAUSED:
            running = step.resume()
            if not await review_repo.advance_step(
                running, ReviewStepStatus.PAUSED.value
            ) or not await review_repo.advance_step(
                running.succeed(refs),
                ReviewStepStatus.RUNNING.value,
            ):
                raise RunConcurrentModificationError(review.run_id)
        elif step.status is not ReviewStepStatus.SUCCEEDED or step.output_refs != refs:
            raise RunConcurrentModificationError(review.run_id)
        if review.current_stage in {
            ReviewStage.VALIDATE_REQUEST,
            ReviewStage.IMPORT_ARXIV_PAPERS,
            ReviewStage.WAIT_FOR_INGESTION,
        }:
            advanced = replace(
                review,
                current_stage=ReviewStage.BUILD_EVIDENCE_MATRIX,
                updated_at=datetime.now(UTC),
            )
            if not await review_repo.advance_review_stage(
                advanced, expected_stage=review.current_stage.value
            ):
                raise RunConcurrentModificationError(review.run_id)

    @staticmethod
    def _minimum_ready_papers(review: ReviewRun) -> int:
        value = review.config_snapshot.get("minimum_ready_papers", 1)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return 1
        return value

    async def _evaluate_source(
        self,
        session: TSession,
        parent: Run,
        source: ReviewSource,
        dependencies: list[ReviewDependency],
    ) -> tuple[ChunkSet | None, str | None]:
        if source.status is ReviewSourceStatus.DISCOVERED:
            return None, "review_source_not_imported"
        if source.paper_id is None or source.paper_version_id is None:
            return None, "review_source_binding_missing"
        paper = await self._paper_repo_factory(session).get_by_id(source.paper_id)
        version = await self._paper_version_repo_factory(session).get_by_id(
            source.paper_version_id
        )
        relation = await self._project_paper_repo_factory(session).get_by_version(
            parent.project_id, source.paper_version_id
        )
        if (
            paper is None
            or paper.owner_id != parent.owner_id
            or version is None
            or version.owner_id != parent.owner_id
            or version.paper_id != source.paper_id
            or relation is None
            or relation.paper_id != source.paper_id
        ):
            return None, "review_source_scope_mismatch"
        if not any(
            dependency.dependency_type is ReviewDependencyType.PAPER_VERSION
            and dependency.target_paper_version_id == version.version_id
            for dependency in dependencies
        ):
            return None, "paper_version_dependency_missing"
        ready = await self._chunk_set_repo_factory(session).get_ready_by_version(
            version.version_id
        )
        if ready is not None:
            return ready, None
        if version.ingestion_run_id is None:
            return None, "ingestion_run_missing"
        ingestion = await self._run_repo_factory(session).get_by_id(
            version.ingestion_run_id
        )
        if (
            ingestion is None
            or ingestion.owner_id != parent.owner_id
            or ingestion.run_type != RunType.INGESTION.value
        ):
            return None, "ingestion_run_scope_mismatch"
        if ingestion.project_id == parent.project_id and not any(
            dependency.dependency_type is ReviewDependencyType.RUN
            and dependency.target_run_id == ingestion.run_id
            for dependency in dependencies
        ):
            return None, "ingestion_dependency_missing"
        if ingestion.status in _ACTIVE_CHILD_STATUSES:
            return None, None
        if ingestion.status is RunStatus.FAILED:
            return None, "ingestion_failed"
        if ingestion.status is RunStatus.CANCELLED:
            return None, "ingestion_cancelled"
        if ingestion.status is not RunStatus.SUCCEEDED:
            return None, "ingestion_status_invalid"
        if version.current_parse_revision_id is None:
            return None, "parse_revision_missing_after_ingestion"
        revision = await self._parse_revision_repo_factory(session).get_by_id(
            version.current_parse_revision_id
        )
        if revision is None or revision.version_id != version.version_id:
            return None, "parse_revision_scope_mismatch"
        if revision.status is not ParseRevisionStatus.SUCCEEDED:
            return None, "parse_revision_not_ready_after_ingestion"
        indexing_id = await self._run_repo_factory(session).get_latest_indexing_run_id(
            revision.revision_id
        )
        if indexing_id is None:
            return None, "indexing_run_missing_after_ingestion"
        indexing = await self._run_repo_factory(session).get_by_id(indexing_id)
        if (
            indexing is None
            or indexing.owner_id != parent.owner_id
            or indexing.project_id != ingestion.project_id
            or indexing.run_type != RunType.INDEXING.value
        ):
            return None, "indexing_run_scope_mismatch"
        if indexing.status in _ACTIVE_CHILD_STATUSES:
            return None, None
        if indexing.status is RunStatus.FAILED:
            return None, "indexing_failed"
        if indexing.status is RunStatus.CANCELLED:
            return None, "indexing_cancelled"
        if indexing.status is RunStatus.SUCCEEDED:
            return None, "chunk_set_missing_after_indexing"
        return None, "indexing_status_invalid"

    async def _advance_run_dependencies(
        self,
        session: TSession,
        review_repo: ReviewRepository,
        dependencies: list[ReviewDependency],
        parent: Run,
    ) -> None:
        run_repo = self._run_repo_factory(session)
        for dependency in dependencies:
            if (
                dependency.dependency_type is not ReviewDependencyType.RUN
                or dependency.status is not ReviewDependencyStatus.PENDING
                or dependency.target_run_id is None
            ):
                continue
            child = await run_repo.get_by_id(dependency.target_run_id)
            if child is None:
                await review_repo.save_dependency(
                    dependency.mark_failed("dependency_run_missing")
                )
            elif (
                child.owner_id != parent.owner_id
                or child.project_id != parent.project_id
                or child.run_type != RunType.INGESTION.value
            ):
                # Slice 3 只会为同 Project 的 ingestion 创建 RUN 依赖；
                # 跨 Project Version 复用仅保留 PaperVersion 依赖。
                await review_repo.save_dependency(
                    dependency.mark_failed("dependency_run_scope_mismatch")
                )
            elif child.status is RunStatus.SUCCEEDED:
                await review_repo.save_dependency(dependency.mark_satisfied())
            elif child.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                await review_repo.save_dependency(
                    dependency.mark_failed(f"dependency_run_{child.status.value}")
                )

    @staticmethod
    async def _mark_version_dependency(
        review_repo: ReviewRepository,
        dependencies: list[ReviewDependency],
        version_id: str,
        failure_code: str | None,
    ) -> list[ReviewDependency]:
        updated_dependencies = list(dependencies)
        for index, dependency in enumerate(updated_dependencies):
            if (
                dependency.dependency_type is ReviewDependencyType.PAPER_VERSION
                and dependency.target_paper_version_id == version_id
                and dependency.status is ReviewDependencyStatus.PENDING
            ):
                updated = (
                    dependency.mark_failed(failure_code)
                    if failure_code is not None
                    else dependency.mark_satisfied()
                )
                await review_repo.save_dependency(updated)
                updated_dependencies[index] = updated
        return updated_dependencies

    async def _append_event(
        self,
        session: TSession,
        run: Run,
        event_type: str,
        payload: dict,
    ) -> Run:
        if not await self._run_repo_factory(session).update_status(
            run.run_id,
            run.status,
            run.status,
            run.event_sequence + 1,
        ):
            raise RunConcurrentModificationError(run.run_id)
        await self._event_repo_factory(session).add(
            create_event(
                run_id=run.run_id,
                sequence=run.event_sequence,
                event_type=event_type,
                actor_type="system",
                correlation_id=f"dependency-reconcile:{run.run_id}",
                payload=payload,
            )
        )
        return replace(run, event_sequence=run.event_sequence + 1)
