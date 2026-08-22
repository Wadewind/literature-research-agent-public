"""Review Workflow 聚合的 PostgreSQL Repository。"""

from typing import cast

from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.domain.review import (
    Artifact,
    ArtifactType,
    HumanInput,
    HumanInputAction,
    HumanInputRequest,
    HumanInputRequestStatus,
    ReviewDependency,
    ReviewDependencyStatus,
    ReviewDependencyType,
    ReviewOutput,
    ReviewOutputType,
    ReviewRun,
    ReviewSource,
    ReviewSourceStatus,
    ReviewStage,
    ReviewStepKey,
    ReviewStepStatus,
    RunStep,
)
from literature_agent.domain.run import RunStatus, RunType
from literature_agent.infrastructure.persistence.models import (
    ArtifactORM,
    HumanInputORM,
    HumanInputRequestORM,
    ReviewOutputORM,
    ReviewRunORM,
    ReviewSourceORM,
    RunDependencyORM,
    RunORM,
    RunStepORM,
)


def _review_run_to_orm(value: ReviewRun) -> ReviewRunORM:
    return ReviewRunORM(
        run_id=value.run_id,
        research_question=value.research_question,
        workflow_version=value.workflow_version,
        model_profile_version=value.model_profile_version,
        prompt_versions=value.prompt_versions,
        config_snapshot=value.config_snapshot,
        statistics_summary=value.statistics_summary,
        current_stage=value.current_stage.value,
        current_outline_output_id=value.current_outline_output_id,
        final_artifact_id=value.final_artifact_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _review_run_to_domain(value: ReviewRunORM) -> ReviewRun:
    return ReviewRun(
        run_id=value.run_id,
        research_question=value.research_question,
        workflow_version=value.workflow_version,
        model_profile_version=value.model_profile_version,
        prompt_versions=value.prompt_versions,
        config_snapshot=value.config_snapshot,
        statistics_summary=value.statistics_summary,
        current_stage=ReviewStage(value.current_stage),
        current_outline_output_id=value.current_outline_output_id,
        final_artifact_id=value.final_artifact_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _step_to_domain(value: RunStepORM) -> RunStep:
    return RunStep(
        step_id=value.step_id,
        run_id=value.run_id,
        step_key=ReviewStepKey(value.step_key),
        sequence=value.sequence,
        status=ReviewStepStatus(value.status),
        idempotency_key=value.idempotency_key,
        input_refs=value.input_refs,
        output_refs=value.output_refs,
        error_code=value.error_code,
        started_at=value.started_at,
        completed_at=value.completed_at,
        created_at=value.created_at,
    )


def _source_to_domain(value: ReviewSourceORM) -> ReviewSource:
    return ReviewSource(
        source_id=value.source_id,
        review_run_id=value.review_run_id,
        arxiv_id=value.arxiv_id,
        arxiv_version=value.arxiv_version,
        rank=value.rank,
        metadata_snapshot=value.metadata_snapshot,
        status=ReviewSourceStatus(value.status),
        paper_id=value.paper_id,
        paper_version_id=value.paper_version_id,
        failure_code=value.failure_code,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _dependency_to_domain(value: RunDependencyORM) -> ReviewDependency:
    return ReviewDependency(
        dependency_id=value.dependency_id,
        parent_run_id=value.parent_run_id,
        dependency_type=ReviewDependencyType(value.dependency_type),
        status=ReviewDependencyStatus(value.status),
        target_run_id=value.target_run_id,
        target_paper_version_id=value.target_paper_version_id,
        target_chunk_set_id=value.target_chunk_set_id,
        failure_code=value.failure_code,
        created_at=value.created_at,
        satisfied_at=value.satisfied_at,
    )


def _output_to_domain(value: ReviewOutputORM) -> ReviewOutput:
    return ReviewOutput(
        output_id=value.output_id,
        review_run_id=value.review_run_id,
        output_type=ReviewOutputType(value.output_type),
        output_key=value.output_key,
        version=value.version,
        schema_version=value.schema_version,
        payload=value.payload,
        idempotency_key=value.idempotency_key,
        created_at=value.created_at,
    )


def _request_to_domain(value: HumanInputRequestORM) -> HumanInputRequest:
    return HumanInputRequest(
        request_id=value.request_id,
        review_run_id=value.review_run_id,
        request_version=value.request_version,
        outline_output_id=value.outline_output_id,
        status=HumanInputRequestStatus(value.status),
        allowed_actions=tuple(HumanInputAction(x) for x in value.allowed_actions),
        resolved_input_id=value.resolved_input_id,
        created_at=value.created_at,
        resolved_at=value.resolved_at,
    )


def _human_input_to_domain(value: HumanInputORM) -> HumanInput:
    return HumanInput(
        human_input_id=value.human_input_id,
        request_id=value.request_id,
        request_version=value.request_version,
        action=HumanInputAction(value.action),
        payload=value.payload,
        submitted_by=value.submitted_by,
        idempotency_key=value.idempotency_key,
        created_at=value.created_at,
    )


def _artifact_to_domain(value: ArtifactORM) -> Artifact:
    return Artifact(
        artifact_id=value.artifact_id,
        review_run_id=value.review_run_id,
        project_id=value.project_id,
        owner_id=value.owner_id,
        artifact_type=ArtifactType(value.artifact_type),
        storage_key=value.storage_key,
        content_hash=value.content_hash,
        size_bytes=value.size_bytes,
        media_type=value.media_type,
        idempotency_key=value.idempotency_key,
        source_output_id=value.source_output_id,
        metadata=value.artifact_metadata,
        created_at=value.created_at,
    )


class SqlalchemyReviewRepository(ReviewRepository):
    """基于通用 Run 范围过滤的 Review 聚合 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _scope(run_id: str, project_id: str, owner_id: str):
        """生成统一 Project/owner 范围条件。"""
        return (
            ReviewRunORM.run_id == run_id,
            RunORM.project_id == project_id,
            RunORM.owner_id == owner_id,
        )

    async def add_review_run(self, review_run: ReviewRun) -> ReviewRun:
        self._session.add(_review_run_to_orm(review_run))
        return review_run

    async def get_review_run_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> ReviewRun | None:
        result = await self._session.execute(
            select(ReviewRunORM)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(*self._scope(run_id, project_id, owner_id))
        )
        row = result.scalar_one_or_none()
        return _review_run_to_domain(row) if row else None

    async def get_review_run_scoped_for_update(
        self, run_id: str, project_id: str, owner_id: str
    ) -> ReviewRun | None:
        result = await self._session.execute(
            select(ReviewRunORM)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(*self._scope(run_id, project_id, owner_id))
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        return _review_run_to_domain(row) if row else None

    async def advance_review_outline(
        self,
        review_run: ReviewRun,
        *,
        expected_outline_output_id: str | None,
    ) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(ReviewRunORM)
                .where(
                    ReviewRunORM.run_id == review_run.run_id,
                    ReviewRunORM.current_outline_output_id.is_(None)
                    if expected_outline_output_id is None
                    else ReviewRunORM.current_outline_output_id == expected_outline_output_id,
                )
                .values(
                    current_stage=review_run.current_stage.value,
                    current_outline_output_id=review_run.current_outline_output_id,
                    updated_at=review_run.updated_at,
                )
            )
        )
        return result.rowcount == 1

    async def advance_review_stage(self, review_run: ReviewRun, *, expected_stage: str) -> bool:
        """用 Stage 条件更新防止旧节点覆盖后继状态。"""
        result = cast(
            CursorResult,
            await self._session.execute(
                update(ReviewRunORM)
                .where(
                    ReviewRunORM.run_id == review_run.run_id,
                    ReviewRunORM.current_stage == expected_stage,
                )
                .values(
                    current_stage=review_run.current_stage.value,
                    updated_at=review_run.updated_at,
                )
            )
        )
        return result.rowcount == 1

    async def advance_review_final(
        self,
        review_run: ReviewRun,
        *,
        expected_stage: str,
        expected_final_artifact_id: str | None,
    ) -> bool:
        """用 Stage 与最终指针双条件收敛导出提交。"""
        result = cast(
            CursorResult,
            await self._session.execute(
                update(ReviewRunORM)
                .where(
                    ReviewRunORM.run_id == review_run.run_id,
                    ReviewRunORM.current_stage == expected_stage,
                    ReviewRunORM.final_artifact_id.is_(None)
                    if expected_final_artifact_id is None
                    else ReviewRunORM.final_artifact_id == expected_final_artifact_id,
                )
                .values(
                    current_stage=review_run.current_stage.value,
                    final_artifact_id=review_run.final_artifact_id,
                    statistics_summary=review_run.statistics_summary,
                    updated_at=review_run.updated_at,
                )
            ),
        )
        return result.rowcount == 1

    async def list_waiting_dependency_run_ids(self, limit: int) -> list[str]:
        """按创建顺序有界列出等待依赖的 Review Run。"""
        result = await self._session.execute(
            select(ReviewRunORM.run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                RunORM.run_type == RunType.REVIEW.value,
                RunORM.status == RunStatus.WAITING_DEPENDENCY.value,
            )
            .order_by(RunORM.created_at, RunORM.run_id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def add_step(self, step: RunStep) -> RunStep:
        self._session.add(
            RunStepORM(
                step_id=step.step_id,
                run_id=step.run_id,
                step_key=step.step_key.value,
                sequence=step.sequence,
                status=step.status.value,
                idempotency_key=step.idempotency_key,
                input_refs=step.input_refs,
                output_refs=step.output_refs,
                error_code=step.error_code,
                started_at=step.started_at,
                completed_at=step.completed_at,
                created_at=step.created_at,
            )
        )
        return step

    async def get_or_add_step(self, step: RunStep) -> RunStep:
        """用 PostgreSQL 唯一键保证节点重放只产生一条 Step。"""
        values = {
            "step_id": step.step_id,
            "run_id": step.run_id,
            "step_key": step.step_key.value,
            "sequence": step.sequence,
            "status": step.status.value,
            "idempotency_key": step.idempotency_key,
            "input_refs": step.input_refs,
            "output_refs": step.output_refs,
            "error_code": step.error_code,
            "started_at": step.started_at,
            "completed_at": step.completed_at,
            "created_at": step.created_at,
        }
        await self._session.execute(
            postgresql.insert(RunStepORM)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[RunStepORM.run_id, RunStepORM.idempotency_key])
        )
        result = await self._session.execute(
            select(RunStepORM).where(
                RunStepORM.run_id == step.run_id,
                RunStepORM.idempotency_key == step.idempotency_key,
            )
        )
        return _step_to_domain(result.scalar_one())

    async def advance_step(self, step: RunStep, expected_status: str) -> bool:
        """用状态条件更新保证终态不会被旧执行者覆盖。"""
        result = cast(
            CursorResult,
            await self._session.execute(
                update(RunStepORM)
                .where(
                    RunStepORM.step_id == step.step_id,
                    RunStepORM.run_id == step.run_id,
                    RunStepORM.status == expected_status,
                )
                .values(
                    status=step.status.value,
                    output_refs=step.output_refs,
                    error_code=step.error_code,
                    started_at=step.started_at,
                    completed_at=step.completed_at,
                )
            )
        )
        return bool(result.rowcount == 1)

    async def list_steps_scoped(self, run_id: str, project_id: str, owner_id: str) -> list[RunStep]:
        result = await self._session.execute(
            select(RunStepORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == RunStepORM.run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(*self._scope(run_id, project_id, owner_id))
            .order_by(RunStepORM.sequence)
        )
        return [_step_to_domain(x) for x in result.scalars().all()]

    async def add_source(self, source: ReviewSource) -> ReviewSource:
        self._session.add(
            ReviewSourceORM(
                source_id=source.source_id,
                review_run_id=source.review_run_id,
                arxiv_id=source.arxiv_id,
                arxiv_version=source.arxiv_version,
                rank=source.rank,
                metadata_snapshot=source.metadata_snapshot,
                status=source.status.value,
                paper_id=source.paper_id,
                paper_version_id=source.paper_version_id,
                failure_code=source.failure_code,
                created_at=source.created_at,
                updated_at=source.updated_at,
            )
        )
        return source

    async def list_sources_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewSource]:
        result = await self._session.execute(
            select(ReviewSourceORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == ReviewSourceORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(*self._scope(run_id, project_id, owner_id))
            .order_by(ReviewSourceORM.rank)
        )
        return [_source_to_domain(x) for x in result.scalars().all()]

    async def get_source_scoped_for_update(
        self, source_id: str, run_id: str, project_id: str, owner_id: str
    ) -> ReviewSource | None:
        result = await self._session.execute(
            select(ReviewSourceORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == ReviewSourceORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                *self._scope(run_id, project_id, owner_id),
                ReviewSourceORM.source_id == source_id,
                ReviewSourceORM.review_run_id == run_id,
            )
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        return _source_to_domain(row) if row else None

    async def save_source(self, source: ReviewSource) -> None:
        result = await self._session.execute(
            select(ReviewSourceORM).where(ReviewSourceORM.source_id == source.source_id)
        )
        row = result.scalar_one()
        row.status = source.status.value
        row.paper_id = source.paper_id
        row.paper_version_id = source.paper_version_id
        row.failure_code = source.failure_code
        row.updated_at = source.updated_at

    async def add_dependency(self, dependency: ReviewDependency) -> ReviewDependency:
        self._session.add(
            RunDependencyORM(
                dependency_id=dependency.dependency_id,
                parent_run_id=dependency.parent_run_id,
                dependency_type=dependency.dependency_type.value,
                status=dependency.status.value,
                target_run_id=dependency.target_run_id,
                target_paper_version_id=dependency.target_paper_version_id,
                target_chunk_set_id=dependency.target_chunk_set_id,
                failure_code=dependency.failure_code,
                created_at=dependency.created_at,
                satisfied_at=dependency.satisfied_at,
            )
        )
        return dependency

    async def list_dependencies_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewDependency]:
        result = await self._session.execute(
            select(RunDependencyORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == RunDependencyORM.parent_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(*self._scope(run_id, project_id, owner_id))
            .order_by(RunDependencyORM.created_at)
        )
        return [_dependency_to_domain(x) for x in result.scalars().all()]

    async def save_dependency(self, dependency: ReviewDependency) -> None:
        result = await self._session.execute(
            select(RunDependencyORM).where(
                RunDependencyORM.dependency_id == dependency.dependency_id
            )
        )
        row = result.scalar_one()
        row.status = dependency.status.value
        row.failure_code = dependency.failure_code
        row.satisfied_at = dependency.satisfied_at

    async def add_output(self, output: ReviewOutput) -> ReviewOutput:
        self._session.add(
            ReviewOutputORM(
                output_id=output.output_id,
                review_run_id=output.review_run_id,
                output_type=output.output_type.value,
                output_key=output.output_key,
                version=output.version,
                schema_version=output.schema_version,
                payload=output.payload,
                idempotency_key=output.idempotency_key,
                created_at=output.created_at,
            )
        )
        return output

    async def get_or_add_output(self, output: ReviewOutput) -> ReviewOutput:
        """用 PostgreSQL 唯一键保证节点重放不追加重复 Output。"""
        values = {
            "output_id": output.output_id,
            "review_run_id": output.review_run_id,
            "output_type": output.output_type.value,
            "output_key": output.output_key,
            "version": output.version,
            "schema_version": output.schema_version,
            "payload": output.payload,
            "idempotency_key": output.idempotency_key,
            "created_at": output.created_at,
        }
        await self._session.execute(
            postgresql.insert(ReviewOutputORM)
            .values(**values)
            .on_conflict_do_nothing()
        )
        result = await self._session.execute(
            select(ReviewOutputORM).where(
                ReviewOutputORM.review_run_id == output.review_run_id,
                ReviewOutputORM.idempotency_key == output.idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            # 另一唯一键先命中时仍返回冲突行，让应用层比较稳定语义并报告幂等冲突。
            result = await self._session.execute(
                select(ReviewOutputORM).where(
                    ReviewOutputORM.review_run_id == output.review_run_id,
                    ReviewOutputORM.output_type == output.output_type.value,
                    ReviewOutputORM.output_key == output.output_key,
                    ReviewOutputORM.version == output.version,
                )
            )
            row = result.scalar_one()
        return _output_to_domain(row)

    async def list_outputs_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewOutput]:
        result = await self._session.execute(
            select(ReviewOutputORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == ReviewOutputORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(*self._scope(run_id, project_id, owner_id))
            .order_by(
                ReviewOutputORM.output_type,
                ReviewOutputORM.output_key,
                ReviewOutputORM.version,
            )
        )
        return [_output_to_domain(x) for x in result.scalars().all()]

    async def add_human_input_request(self, request: HumanInputRequest) -> HumanInputRequest:
        self._session.add(
            HumanInputRequestORM(
                request_id=request.request_id,
                review_run_id=request.review_run_id,
                request_version=request.request_version,
                outline_output_id=request.outline_output_id,
                status=request.status.value,
                allowed_actions=[x.value for x in request.allowed_actions],
                resolved_input_id=request.resolved_input_id,
                created_at=request.created_at,
                resolved_at=request.resolved_at,
            )
        )
        return request

    async def get_or_add_human_input_request(self, request: HumanInputRequest) -> HumanInputRequest:
        values = {
            "request_id": request.request_id,
            "review_run_id": request.review_run_id,
            "request_version": request.request_version,
            "outline_output_id": request.outline_output_id,
            "status": request.status.value,
            "allowed_actions": [item.value for item in request.allowed_actions],
            "resolved_input_id": request.resolved_input_id,
            "created_at": request.created_at,
            "resolved_at": request.resolved_at,
        }
        await self._session.execute(
            postgresql.insert(HumanInputRequestORM).values(**values).on_conflict_do_nothing()
        )
        result = await self._session.execute(
            select(HumanInputRequestORM).where(
                HumanInputRequestORM.review_run_id == request.review_run_id,
                HumanInputRequestORM.request_version == request.request_version,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            result = await self._session.execute(
                select(HumanInputRequestORM).where(
                    HumanInputRequestORM.review_run_id == request.review_run_id,
                    HumanInputRequestORM.status == HumanInputRequestStatus.OPEN.value,
                )
            )
            row = result.scalar_one()
        return _request_to_domain(row)

    async def get_open_human_input_request_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> HumanInputRequest | None:
        result = await self._session.execute(
            select(HumanInputRequestORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == HumanInputRequestORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                *self._scope(run_id, project_id, owner_id),
                HumanInputRequestORM.status == HumanInputRequestStatus.OPEN.value,
            )
        )
        row = result.scalar_one_or_none()
        return _request_to_domain(row) if row else None

    async def get_human_input_request_scoped_for_update(
        self,
        request_id: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> HumanInputRequest | None:
        result = await self._session.execute(
            select(HumanInputRequestORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == HumanInputRequestORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                *self._scope(run_id, project_id, owner_id),
                HumanInputRequestORM.request_id == request_id,
                HumanInputRequestORM.review_run_id == run_id,
            )
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        return _request_to_domain(row) if row else None

    async def resolve_human_input_request(
        self, request: HumanInputRequest, *, expected_status: str
    ) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(HumanInputRequestORM)
                .where(
                    HumanInputRequestORM.request_id == request.request_id,
                    HumanInputRequestORM.status == expected_status,
                )
                .values(
                    status=request.status.value,
                    resolved_input_id=request.resolved_input_id,
                    resolved_at=request.resolved_at,
                )
            ),
        )
        return result.rowcount == 1

    async def add_human_input(self, human_input: HumanInput) -> HumanInput:
        self._session.add(
            HumanInputORM(
                human_input_id=human_input.human_input_id,
                request_id=human_input.request_id,
                request_version=human_input.request_version,
                action=human_input.action.value,
                payload=human_input.payload,
                submitted_by=human_input.submitted_by,
                idempotency_key=human_input.idempotency_key,
                created_at=human_input.created_at,
            )
        )
        return human_input

    async def get_or_add_human_input(self, human_input: HumanInput) -> HumanInput:
        values = {
            "human_input_id": human_input.human_input_id,
            "request_id": human_input.request_id,
            "request_version": human_input.request_version,
            "action": human_input.action.value,
            "payload": human_input.payload,
            "submitted_by": human_input.submitted_by,
            "idempotency_key": human_input.idempotency_key,
            "created_at": human_input.created_at,
        }
        await self._session.execute(
            postgresql.insert(HumanInputORM).values(**values).on_conflict_do_nothing()
        )
        result = await self._session.execute(
            select(HumanInputORM).where(
                HumanInputORM.submitted_by == human_input.submitted_by,
                HumanInputORM.idempotency_key == human_input.idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            result = await self._session.execute(
                select(HumanInputORM).where(HumanInputORM.request_id == human_input.request_id)
            )
            row = result.scalar_one()
        return _human_input_to_domain(row)

    async def get_human_input_scoped(
        self,
        human_input_id: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> HumanInput | None:
        result = await self._session.execute(
            select(HumanInputORM)
            .join(
                HumanInputRequestORM,
                HumanInputRequestORM.request_id == HumanInputORM.request_id,
            )
            .join(ReviewRunORM, ReviewRunORM.run_id == HumanInputRequestORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                *self._scope(run_id, project_id, owner_id),
                HumanInputORM.human_input_id == human_input_id,
            )
        )
        row = result.scalar_one_or_none()
        return _human_input_to_domain(row) if row else None

    async def get_human_input_by_idempotency_scoped(
        self,
        submitted_by: str,
        idempotency_key: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> HumanInput | None:
        result = await self._session.execute(
            select(HumanInputORM)
            .join(
                HumanInputRequestORM,
                HumanInputRequestORM.request_id == HumanInputORM.request_id,
            )
            .join(ReviewRunORM, ReviewRunORM.run_id == HumanInputRequestORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                *self._scope(run_id, project_id, owner_id),
                HumanInputORM.submitted_by == submitted_by,
                HumanInputORM.idempotency_key == idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        return _human_input_to_domain(row) if row else None

    async def get_latest_resolved_human_input_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> tuple[HumanInputRequest, HumanInput] | None:
        result = await self._session.execute(
            select(HumanInputRequestORM, HumanInputORM)
            .join(RunORM, RunORM.run_id == HumanInputRequestORM.review_run_id)
            .join(
                HumanInputORM,
                HumanInputORM.human_input_id == HumanInputRequestORM.resolved_input_id,
            )
            .where(
                HumanInputRequestORM.review_run_id == run_id,
                HumanInputRequestORM.status == HumanInputRequestStatus.RESOLVED.value,
                RunORM.project_id == project_id,
                RunORM.owner_id == owner_id,
            )
            .order_by(HumanInputRequestORM.request_version.desc())
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return _request_to_domain(row[0]), _human_input_to_domain(row[1])

    async def add_artifact(self, artifact: Artifact) -> Artifact:
        self._session.add(
            ArtifactORM(
                artifact_id=artifact.artifact_id,
                review_run_id=artifact.review_run_id,
                project_id=artifact.project_id,
                owner_id=artifact.owner_id,
                artifact_type=artifact.artifact_type.value,
                storage_key=artifact.storage_key,
                content_hash=artifact.content_hash,
                size_bytes=artifact.size_bytes,
                media_type=artifact.media_type,
                idempotency_key=artifact.idempotency_key,
                source_output_id=artifact.source_output_id,
                artifact_metadata=artifact.metadata,
                created_at=artifact.created_at,
            )
        )
        return artifact

    async def get_or_add_artifact(self, artifact: Artifact) -> Artifact:
        """用唯一键收敛 Artifact 重放，并返回数据库赢家。"""
        values = {
            "artifact_id": artifact.artifact_id,
            "review_run_id": artifact.review_run_id,
            "project_id": artifact.project_id,
            "owner_id": artifact.owner_id,
            "artifact_type": artifact.artifact_type.value,
            "storage_key": artifact.storage_key,
            "content_hash": artifact.content_hash,
            "size_bytes": artifact.size_bytes,
            "media_type": artifact.media_type,
            "idempotency_key": artifact.idempotency_key,
            "source_output_id": artifact.source_output_id,
            "artifact_metadata": artifact.metadata,
            "created_at": artifact.created_at,
        }
        await self._session.execute(
            postgresql.insert(ArtifactORM).values(**values).on_conflict_do_nothing()
        )
        result = await self._session.execute(
            select(ArtifactORM).where(
                ArtifactORM.review_run_id == artifact.review_run_id,
                ArtifactORM.idempotency_key == artifact.idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            result = await self._session.execute(
                select(ArtifactORM).where(ArtifactORM.storage_key == artifact.storage_key)
            )
            row = result.scalar_one()
        return _artifact_to_domain(row)

    async def get_artifact_scoped(
        self,
        artifact_id: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> Artifact | None:
        result = await self._session.execute(
            select(ArtifactORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == ArtifactORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                *self._scope(run_id, project_id, owner_id),
                ArtifactORM.artifact_id == artifact_id,
                ArtifactORM.review_run_id == run_id,
                ArtifactORM.project_id == project_id,
                ArtifactORM.owner_id == owner_id,
            )
        )
        row = result.scalar_one_or_none()
        return _artifact_to_domain(row) if row else None

    async def list_artifacts_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[Artifact]:
        result = await self._session.execute(
            select(ArtifactORM)
            .join(ReviewRunORM, ReviewRunORM.run_id == ArtifactORM.review_run_id)
            .join(RunORM, RunORM.run_id == ReviewRunORM.run_id)
            .where(
                *self._scope(run_id, project_id, owner_id),
                ArtifactORM.project_id == project_id,
                ArtifactORM.owner_id == owner_id,
            )
            .order_by(ArtifactORM.created_at)
        )
        return [_artifact_to_domain(x) for x in result.scalars().all()]
