"""RunStep 节点副作用的 PostgreSQL 幂等测试。"""

import asyncio

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.review_step_service import ReviewStepService
from literature_agent.domain.exceptions import IdempotencyConflictError
from literature_agent.domain.project import create_project
from literature_agent.domain.review import ReviewStepKey, create_review_run
from literature_agent.domain.run import create_run
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project("user-1", "Step 幂等测试", "")
        await SqlalchemyProjectRepository(session).add(project)
        await session.flush()
        run = create_run(project.project_id, "user-1", "review")
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        await SqlalchemyReviewRepository(session).add_review_run(
            create_review_run(
                run_id=run.run_id,
                research_question="测试幂等",
                workflow_version="review.v1",
                model_profile_version="review-default.v1",
                prompt_versions={"search": "search.v1"},
                config_snapshot={},
            )
        )
        await session.commit()
    return factory, project, run


async def test_concurrent_same_step_converges_and_semantic_conflict_is_rejected(
    db_engine,
) -> None:
    factory, project, run = await _seed(db_engine)

    def service() -> ReviewStepService:
        return ReviewStepService(factory, SqlalchemyReviewRepository)

    arguments = {
        "run_id": run.run_id,
        "project_id": project.project_id,
        "owner_id": "user-1",
        "step_key": ReviewStepKey.FORMULATE_SEARCH_STRATEGY,
        "sequence": 2,
        "idempotency_key": "search:v1",
        "input_refs": {"workflow_version": "review.v1"},
    }
    first, second = await asyncio.gather(
        service().ensure_step(**arguments), service().ensure_step(**arguments)
    )
    assert first.step_id == second.step_id

    with pytest.raises(IdempotencyConflictError):
        await service().ensure_step(
            **{**arguments, "input_refs": {"workflow_version": "review.v2"}}
        )

    # 相同 sequence 但不同幂等键是另一项业务副作用，必须由 sequence 唯一约束拒绝，
    # 不能被 idempotency ON CONFLICT 静默吞掉。
    with pytest.raises(IntegrityError):
        await service().ensure_step(
            **{**arguments, "idempotency_key": "different-key"}
        )

    async with factory() as session:
        rows = await SqlalchemyReviewRepository(session).list_steps_scoped(
            run.run_id, project.project_id, "user-1"
        )
    assert len(rows) == 1
