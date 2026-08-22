"""Review Artifact PostgreSQL 幂等与范围隔离测试。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.domain.project import create_project
from literature_agent.domain.review import (
    ArtifactType,
    ReviewStage,
    create_artifact,
    create_review_run,
)
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository


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
