"""Review Artifact PostgreSQL 幂等与范围隔离测试。"""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from literature_agent.application.review_export_service import ReviewExportService
from literature_agent.application.run_service import RunService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import RunNotFoundError
from literature_agent.domain.project import create_project
from literature_agent.domain.review import (
    ArtifactType,
    ReviewOutputType,
    ReviewStage,
    create_artifact,
    create_review_output,
    create_review_run,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.model_invocation_repository import (
    SqlalchemyModelInvocationRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from tests.fakes.fake_storage import FakeStorage


def _manifest(run_id: str) -> list[dict]:
    content = b"# review"
    return [
        {
            "artifact_type": ArtifactType.REVIEW_MARKDOWN.value,
            "storage_key": f"user-1/reviews/{run_id}/stable/review.md",
            "content_hash": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "media_type": "text/markdown",
        }
    ]


def _output(run_id: str):
    return create_review_output(
        review_run_id=run_id,
        output_type=ReviewOutputType.FINAL_REVIEW,
        output_key="final-review",
        version=1,
        schema_version="final-review.v1",
        payload={
            "approved_outline_output_id": "outline-1",
            "evidence_matrix_output_id": "matrix-1",
            "section_output_ids": ["section-1"],
            "claim_set_id": "claims-1",
            "consistency_output_id": "consistency-1",
            "statistics": {},
        },
        idempotency_key=f"{run_id}:final-review:review-markdown.v1",
    )


def _export_service(factory, storage, run_repo_factory=SqlalchemyRunRepository):
    return ReviewExportService(
        session_factory=factory,
        run_repo_factory=run_repo_factory,
        review_repo_factory=SqlalchemyReviewRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        event_repo_factory=SqlalchemyEventRepository,
        model_invocation_repo_factory=SqlalchemyModelInvocationRepository,
        storage=storage,
    )


async def _seed_export_boundary(factory):
    async with factory() as session:
        project = create_project("user-1", "Artifact 事务", "")
        await SqlalchemyProjectRepository(session).add(project)
        await session.flush()
        run = create_run(project.project_id, "user-1", RunType.REVIEW).transition_to(
            RunStatus.RUNNING
        )
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        await SqlalchemyReviewRepository(session).add_review_run(
            replace(
                create_review_run(
                    run_id=run.run_id,
                    research_question="问题",
                    workflow_version="review.v1",
                    model_profile_version="review-default.v1",
                    prompt_versions={"search": "search.v1"},
                    config_snapshot={},
                ),
                current_stage=ReviewStage.EXPORT_REVIEW,
            )
        )
        await session.commit()
    return project, run


async def test_concurrent_artifact_write_converges_and_final_pointer_is_scoped(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project("user-1", "Artifact 幂等", "")
        await SqlalchemyProjectRepository(session).add(project)
        await session.flush()
        run = create_run(project.project_id, "user-1", RunType.REVIEW)
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        review = replace(
            create_review_run(
                run_id=run.run_id,
                research_question="问题",
                workflow_version="review.v1",
                model_profile_version="review-default.v1",
                prompt_versions={"search": "search.v1"},
                config_snapshot={},
            ),
            current_stage=ReviewStage.EXPORT_REVIEW,
        )
        await SqlalchemyReviewRepository(session).add_review_run(review)
        await session.commit()

    def proposed():
        return create_artifact(
            review_run_id=run.run_id,
            project_id=project.project_id,
            owner_id="user-1",
            artifact_type=ArtifactType.REVIEW_MARKDOWN,
            storage_key=f"user-1/reviews/{run.run_id}/hash/review.md",
            content_hash="a" * 64,
            size_bytes=10,
            media_type="text/markdown",
            idempotency_key=f"{run.run_id}:markdown:v1",
        )

    async def persist():
        async with factory() as session:
            value = await SqlalchemyReviewRepository(session).get_or_add_artifact(proposed())
            await session.commit()
            return value

    first, second = await asyncio.gather(persist(), persist())
    assert first.artifact_id == second.artifact_id

    async with factory() as session:
        repo = SqlalchemyReviewRepository(session)
        assert len(
            await repo.list_artifacts_scoped(run.run_id, project.project_id, "user-1")
        ) == 1
        assert (
            await repo.get_artifact_scoped(
                first.artifact_id, run.run_id, project.project_id, "user-2"
            )
            is None
        )
        current = await repo.get_review_run_scoped_for_update(
            run.run_id, project.project_id, "user-1"
        )
        assert current is not None
        final = replace(
            current,
            current_stage=ReviewStage.FINALIZE,
            final_artifact_id=first.artifact_id,
            statistics_summary={
                **current.statistics_summary,
                "model_invocations": 3,
                "prompt_tokens": 120,
                "completion_tokens": 30,
            },
            updated_at=datetime.now(UTC),
        )
        assert await repo.advance_review_final(
            final,
            expected_stage=ReviewStage.EXPORT_REVIEW.value,
            expected_final_artifact_id=None,
        )
        assert not await repo.advance_review_final(
            final,
            expected_stage=ReviewStage.EXPORT_REVIEW.value,
            expected_final_artifact_id=None,
        )
        await session.commit()
    async with factory() as session:
        persisted = await SqlalchemyReviewRepository(session).get_review_run_scoped(
            run.run_id, project.project_id, "user-1"
        )
        assert persisted is not None
        assert persisted.statistics_summary["model_invocations"] == 3
        assert persisted.statistics_summary["prompt_tokens"] == 120
        assert persisted.statistics_summary["completion_tokens"] == 30


async def test_commit_failure_leaves_only_reusable_storage_cache(db_engine) -> None:
    """Storage 成功后真实 PostgreSQL commit 失败，业务事实全回滚并可重放。"""
    normal_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    project, run = await _seed_export_boundary(normal_factory)
    storage = FakeStorage()
    manifest = _manifest(run.run_id)
    await storage.write(manifest[0]["storage_key"], b"# review")

    class FailingCommitSession(AsyncSession):
        async def commit(self) -> None:
            await self.flush()
            raise RuntimeError("模拟 PostgreSQL commit 失败")

    failing_factory = async_sessionmaker(
        db_engine,
        class_=FailingCommitSession,
        expire_on_commit=False,
    )
    with pytest.raises(RuntimeError, match="commit 失败"):
        await _export_service(failing_factory, storage)._commit_export(
            _output(run.run_id),
            manifest,
            project_id=project.project_id,
            owner_id="user-1",
            correlation_id="commit-failure",
        )

    async with normal_factory() as session:
        review_repo = SqlalchemyReviewRepository(session)
        review = await review_repo.get_review_run_scoped(
            run.run_id, project.project_id, "user-1"
        )
        persisted_run = await SqlalchemyRunRepository(session).get_by_id(run.run_id)
        assert review is not None and review.current_stage is ReviewStage.EXPORT_REVIEW
        assert review.final_artifact_id is None
        assert await review_repo.list_outputs_scoped(
            run.run_id, project.project_id, "user-1"
        ) == []
        assert await review_repo.list_artifacts_scoped(
            run.run_id, project.project_id, "user-1"
        ) == []
        assert await review_repo.list_steps_scoped(
            run.run_id, project.project_id, "user-1"
        ) == []
        assert await SqlalchemyEventRepository(session).list_by_run(run.run_id) == []
        assert persisted_run is not None
        assert persisted_run.status is RunStatus.RUNNING
        assert persisted_run.event_sequence == run.event_sequence
    assert await storage.read(manifest[0]["storage_key"]) == b"# review"

    # 重放覆盖同一稳定 key，不新增缓存对象，并收敛为一组业务事实。
    await storage.write(manifest[0]["storage_key"], b"# review")
    output, created, artifacts = await _export_service(
        normal_factory, storage
    )._commit_export(
        _output(run.run_id),
        manifest,
        project_id=project.project_id,
        owner_id="user-1",
        correlation_id="commit-replay",
    )
    assert created is True
    assert output.output_type is ReviewOutputType.FINAL_REVIEW
    assert len(artifacts) == 1
    assert len(storage._objects) == 1

    async with normal_factory() as session:
        review_repo = SqlalchemyReviewRepository(session)
        assert len(
            await review_repo.list_outputs_scoped(
                run.run_id, project.project_id, "user-1"
            )
        ) == 1
        assert len(
            await review_repo.list_artifacts_scoped(
                run.run_id, project.project_id, "user-1"
            )
        ) == 1
        assert [
            event.event_type
            for event in await SqlalchemyEventRepository(session).list_by_run(run.run_id)
        ] == ["review_artifact_created"]


async def test_cancel_lock_wins_export_commit_without_partial_effects(db_engine) -> None:
    """取消先持有 Run 行锁时，导出提交只能失败且不得留下部分业务事实。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    project, run = await _seed_export_boundary(factory)
    storage = FakeStorage()
    manifest = _manifest(run.run_id)
    await storage.write(manifest[0]["storage_key"], b"# review")
    cancel_locked = asyncio.Event()
    export_attempted = asyncio.Event()

    class CancelFirstRunRepository(SqlalchemyRunRepository):
        async def get_by_id_for_update(self, run_id: str, owner_id: str):
            value = await super().get_by_id_for_update(run_id, owner_id)
            cancel_locked.set()
            await export_attempted.wait()
            return value

    class ExportWaitingRunRepository(SqlalchemyRunRepository):
        async def get_by_id_for_update(self, run_id: str, owner_id: str):
            export_attempted.set()
            return await super().get_by_id_for_update(run_id, owner_id)

    cancel_service = RunService(
        session_factory=factory,
        run_repo_factory=CancelFirstRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )
    cancel_task = asyncio.create_task(
        cancel_service.cancel_run(ActorContext("user-1"), run.run_id, "cancel-first")
    )
    await cancel_locked.wait()
    export_task = asyncio.create_task(
        _export_service(factory, storage, ExportWaitingRunRepository)._commit_export(
            _output(run.run_id),
            manifest,
            project_id=project.project_id,
            owner_id="user-1",
            correlation_id="export-second",
        )
    )
    cancelled = await cancel_task
    assert cancelled.status is RunStatus.CANCEL_REQUESTED
    with pytest.raises(RunNotFoundError):
        await export_task

    async with factory() as session:
        review_repo = SqlalchemyReviewRepository(session)
        review = await review_repo.get_review_run_scoped(
            run.run_id, project.project_id, "user-1"
        )
        assert review is not None and review.current_stage is ReviewStage.EXPORT_REVIEW
        assert review.final_artifact_id is None
        assert await review_repo.list_outputs_scoped(
            run.run_id, project.project_id, "user-1"
        ) == []
        assert await review_repo.list_artifacts_scoped(
            run.run_id, project.project_id, "user-1"
        ) == []
        assert await review_repo.list_steps_scoped(
            run.run_id, project.project_id, "user-1"
        ) == []
        events = await SqlalchemyEventRepository(session).list_by_run(run.run_id)
        assert [event.event_type for event in events] == ["run_cancel_requested"]
