"""SqlalchemyAgentRepository 的真实 PostgreSQL 幂等行为测试。"""

from dataclasses import replace

import pytest

from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.research_agent import (
    RuntimeSessionBinding,
    create_agent_artifact_candidate,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine


@pytest.mark.asyncio
async def test_repository_reads_exact_binding_generation_and_converges_candidate_fact(
    db_engine,
) -> None:
    """旧 generation 重放不能漂移，turn/hash 重放只能收敛到同一事实。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="Repository 行为",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="repository-turn-1",
        correlation_id="repository-submit",
    )
    generation_one = RuntimeSessionBinding(
        session_id=agent_session.session_id,
        binding_id="repository-binding-1",
        generation=1,
        runtime_thread_id="repository-thread-1",
        runtime_workspace_id="repository-workspace-1",
    )
    generation_two = replace(
        generation_one,
        binding_id="repository-binding-2",
        generation=2,
        runtime_thread_id="repository-thread-2",
        runtime_workspace_id="repository-workspace-2",
    )
    candidate = create_agent_artifact_candidate(
        candidate_id="repository-candidate-1",
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        session_id=agent_session.session_id,
        turn_run_id=submitted.run_id,
        name="notes.md",
        media_type="text/markdown",
        content_ref="runtime://repository-candidate-1",
        content_hash="d" * 64,
        size_bytes=12,
    )

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        assert await repo.get_or_add_session_binding(generation_one) == generation_one
        assert await repo.get_or_add_session_binding(generation_two) == generation_two
        assert await repo.get_or_add_session_binding(generation_one) == generation_one
        assert (
            await repo.get_session_binding_generation(agent_session.session_id, 1) == generation_one
        )
        assert await repo.get_session_binding(agent_session.session_id) == generation_two
        saved = await repo.get_or_add_candidate(candidate)
        alias = replace(candidate, candidate_id="repository-candidate-alias")
        assert await repo.get_or_add_candidate(alias) == saved
        await session.commit()


@pytest.mark.asyncio
async def test_repository_rejects_candidate_id_collision_with_different_scope(db_engine) -> None:
    """Runtime 复用 candidate_id 时不能把其他 owner/事实冒充为当前 Turn 结果。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="Candidate 碰撞",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="repository-collision-turn",
        correlation_id="repository-collision",
    )
    candidate = create_agent_artifact_candidate(
        candidate_id="repository-collision",
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        session_id=agent_session.session_id,
        turn_run_id=submitted.run_id,
        name="notes.md",
        media_type="text/markdown",
        content_ref="runtime://repository-collision",
        content_hash="e" * 64,
        size_bytes=8,
    )
    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        await repo.get_or_add_candidate(candidate)
        with pytest.raises(RunConcurrentModificationError):
            await repo.get_or_add_candidate(replace(candidate, owner_id="other-owner"))
        await session.rollback()
