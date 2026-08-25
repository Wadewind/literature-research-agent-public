"""SqlalchemyAgentRepository 的真实 PostgreSQL 幂等行为测试。"""

from dataclasses import replace

import pytest

from literature_agent.domain.evidence import AnswerStatus, create_claim_set
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.research_agent import (
    AgentMessageRole,
    RuntimeSessionBinding,
    create_agent_artifact_candidate,
    create_agent_message,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
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


@pytest.mark.asyncio
async def test_agent_message_round_trip_preserves_nullable_claim_set_only_on_message(
    db_engine,
) -> None:
    """claim_set_id 只属于 Message；Session 映射不得读取或写入该列。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="引用结果",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="repository-claim-turn",
        correlation_id="repository-claim",
    )

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        claim_set = create_claim_set(submitted.run_id, AnswerStatus.ANSWERED)
        await SqlalchemyClaimSetRepository(session).add_claim_set(claim_set)
        await session.flush()
        sequence = await repo.allocate_message_sequence(agent_session.session_id)
        assistant = create_agent_message(
            session_id=agent_session.session_id,
            last_sequence=sequence - 1,
            role=AgentMessageRole.ASSISTANT,
            content="结论 [evidence:e-1]",
            turn_run_id=submitted.run_id,
            idempotency_key=f"assistant:{submitted.run_id}",
            claim_set_id=claim_set.claim_set_id,
        )
        await repo.add_message(assistant)
        await session.commit()

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        restored_session = await repo.get_session_scoped(
            agent_session.session_id, scenario.actor.owner_id
        )
        restored_message = await repo.get_message_by_run_and_role(
            submitted.run_id, AgentMessageRole.ASSISTANT.value
        )
        assert restored_session is not None
        assert restored_session.session_id == agent_session.session_id
        assert restored_session.owner_id == agent_session.owner_id
        assert restored_session.project_id == agent_session.project_id
        assert restored_session.active_turn_run_id == submitted.run_id
        assert restored_message is not None
        assert restored_message.claim_set_id == claim_set.claim_set_id
