import asyncio

import pytest

from literature_agent.application.skill_configuration_service import (
    SkillConfigurationService,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    AgentSessionNotFoundError,
    SkillConfigurationInvalidError,
    SkillProfileLockedError,
    SkillProfileRevisionConflictError,
    SkillVersionConflictError,
)
from literature_agent.domain.skill_configuration import (
    SkillProfileSelection,
    SkillSource,
    SkillVersion,
)
from literature_agent.infrastructure.agent.skill_catalog import (
    EVIDENCE_LED_SYNTHESIS,
    PLATFORM_SKILLS,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.skill_repository import (
    SqlalchemySkillRepository,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine


def _service(factory) -> SkillConfigurationService:
    return SkillConfigurationService(
        session_factory=factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        skill_repo_factory=SqlalchemySkillRepository,
        platform_skills=PLATFORM_SKILLS,
    )


@pytest.mark.asyncio
async def test_owner_versions_are_scoped_immutable_and_cas_guarded(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = _service(scenario.factory)
    first = await service.create_owner_skill(
        scenario.actor,
        name="study-comparison",
        description="比较研究设计",
        instructions="先读取 Evidence Matrix。",
        required_tool_names=("read_review_evidence_matrix",),
    )
    assert first.version == 1
    assert EVIDENCE_LED_SYNTHESIS in await service.list_available(scenario.actor)
    assert first in await service.list_available(scenario.actor)
    assert first not in await service.list_available(ActorContext(owner_id="other"))

    results = await asyncio.gather(
        service.create_owner_version(
            scenario.actor,
            first.skill_id,
            expected_version=1,
            description="比较研究设计 A",
            instructions="先读取 Matrix，再核验 Chunk。",
            required_tool_names=(
                "read_review_evidence_matrix",
                "search_project_chunks",
            ),
        ),
        service.create_owner_version(
            scenario.actor,
            first.skill_id,
            expected_version=1,
            description="比较研究设计 B",
            instructions="先核验 Chunk，再读取 Matrix。",
            required_tool_names=(),
        ),
        return_exceptions=True,
    )
    created = [value for value in results if not isinstance(value, Exception)]
    conflicts = [value for value in results if isinstance(value, SkillVersionConflictError)]
    assert len(created) == len(conflicts) == 1
    second = created[0]
    assert isinstance(second, SkillVersion)
    assert second.version == 2 and second.content_hash != first.content_hash

    rollback = await service.create_owner_version(
        scenario.actor,
        first.skill_id,
        expected_version=2,
        description=first.description,
        instructions=first.instructions,
        required_tool_names=first.required_tool_names,
    )
    assert rollback.version == 3
    assert rollback.content_hash == first.content_hash

    async with scenario.factory() as session:
        repo = SqlalchemySkillRepository(session)
        restored_first = await repo.get_owner_version(
            first.skill_id, first.version, scenario.actor.owner_id
        )
        restored_second = await repo.get_owner_version(
            second.skill_id, second.version, scenario.actor.owner_id
        )
        restored_rollback = await repo.get_owner_version(
            rollback.skill_id, rollback.version, scenario.actor.owner_id
        )
    assert restored_first == first
    assert restored_second == second
    assert restored_rollback == rollback

    duplicate_results = await asyncio.gather(
        *(
            service.create_owner_skill(
                scenario.actor,
                name="concurrent-name",
                description="并发创建",
                instructions=f"候选 {index}",
                required_tool_names=(),
            )
            for index in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in duplicate_results) == 1
    assert sum(
        isinstance(value, SkillConfigurationInvalidError)
        for value in duplicate_results
    ) == 1


@pytest.mark.asyncio
async def test_profile_is_owner_scoped_revision_guarded_and_locked_after_first_turn(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent = make_agent_service(
        scenario.factory,
        platform_skills=PLATFORM_SKILLS,
    )
    session_value = await agent.create_session(
        scenario.actor, scenario.project.project_id, title="Skills"
    )
    service = _service(scenario.factory)
    selection = SkillProfileSelection(SkillSource.PLATFORM, EVIDENCE_LED_SYNTHESIS.skill_id, 1)
    profile = await service.update_profile(
        scenario.actor,
        session_value.session_id,
        expected_revision=0,
        selections=(selection,),
    )
    assert profile.revision == 1
    with pytest.raises(SkillProfileRevisionConflictError):
        await service.update_profile(
            scenario.actor,
            session_value.session_id,
            expected_revision=0,
            selections=(),
        )
    with pytest.raises(AgentSessionNotFoundError):
        await service.get_profile(ActorContext(owner_id="other"), session_value.session_id)

    posted = await agent.post_message(
        scenario.actor,
        session_value.session_id,
        content="综合证据",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="skills-turn",
        correlation_id="skills-turn",
    )
    turn = await agent.get_turn(scenario.actor, posted.run_id)
    assert turn.policy_snapshot.allowed_skill_names == ("evidence-led-synthesis",)
    assert turn.policy_snapshot.skill_refs[0].content_hash == EVIDENCE_LED_SYNTHESIS.content_hash
    assert turn.policy_snapshot.skill_refs[0].required_tool_names == (
        "read_review_evidence_matrix",
        "search_project_chunks",
    )
    async with scenario.factory() as session:
        events = await SqlalchemyEventRepository(session).list_by_run(posted.run_id)
    queued = next(event for event in events if event.event_type == "agent_message_accepted")
    assert queued.payload["skill_count"] == 1
    assert "instructions" not in str(queued.payload)
    assert EVIDENCE_LED_SYNTHESIS.instructions not in str(queued.payload)
    with pytest.raises(SkillProfileLockedError):
        await service.update_profile(
            scenario.actor,
            session_value.session_id,
            expected_revision=1,
            selections=(),
        )


@pytest.mark.asyncio
async def test_turn_rejects_skill_that_requires_unselected_mcp_tool(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent = make_agent_service(scenario.factory, platform_skills=PLATFORM_SKILLS)
    session_value = await agent.create_session(
        scenario.actor, scenario.project.project_id, title="Skill permissions"
    )
    service = _service(scenario.factory)
    custom = await service.create_owner_skill(
        scenario.actor,
        name="external-search",
        description="使用外部检索",
        instructions="检索外部元数据。",
        required_tool_names=("arxiv-search_search_papers",),
    )
    await service.update_profile(
        scenario.actor,
        session_value.session_id,
        expected_revision=0,
        selections=(SkillProfileSelection(SkillSource.OWNER, custom.skill_id, 1),),
    )

    with pytest.raises(SkillConfigurationInvalidError, match="权限"):
        await agent.post_message(
            scenario.actor,
            session_value.session_id,
            content="搜索",
            review_output_id=scenario.matrix.output_id,
            idempotency_key="skill-no-expand",
            correlation_id="skill-no-expand",
        )
