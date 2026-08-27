import json

import pytest

from literature_agent.application.mcp_configuration_service import McpConfigurationService
from literature_agent.application.mcp_tool_execution_service import McpToolExecutionService
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    AgentSessionNotFoundError,
    McpProfileInvalidError,
    McpProfileRevisionConflictError,
)
from literature_agent.domain.mcp_configuration import (
    McpCatalog,
    McpCatalogEntry,
    McpParameterSpec,
    McpProfileSelection,
    McpToolContract,
)
from literature_agent.domain.run import RunStatus
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.mcp_profile_repository import (
    SqlalchemyMcpProfileRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from literature_agent.infrastructure.persistence.tool_execution_repository import (
    SqlalchemyToolExecutionRepository,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine


def _catalog() -> McpCatalog:
    return McpCatalog(
        (
            McpCatalogEntry(
                catalog_id="fixture-search",
                version="1.0.0",
                display_name="Fixture Search",
                parameters=(McpParameterSpec("corpus", True, 20),),
                tools=(
                    McpToolContract.from_schema(
                        name="search",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    ),
                    McpToolContract.from_schema(
                        name="lookup",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    ),
                ),
            ),
        )
    )


def _catalog_with_two_versions() -> McpCatalog:
    first = _catalog().entries[0]
    return McpCatalog(
        (
            first,
            McpCatalogEntry(
                catalog_id=first.catalog_id,
                version="2.0.0",
                display_name="Fixture Search v2",
                parameters=first.parameters,
                tools=first.tools,
            ),
        )
    )


@pytest.mark.asyncio
async def test_profile_is_owner_session_scoped_and_revision_guarded(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    session_value = await make_agent_service(scenario.factory).create_session(
        scenario.actor, scenario.project.project_id, title="MCP"
    )
    service = McpConfigurationService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        profile_repo_factory=SqlalchemyMcpProfileRepository,
        catalog=_catalog(),
    )
    empty = await service.get_profile(scenario.actor, session_value.session_id)
    assert empty.revision == 0 and empty.selections == ()

    selection = McpProfileSelection("fixture-search", "1.0.0", (("corpus", "papers"),))
    created = await service.update_profile(
        scenario.actor,
        session_value.session_id,
        expected_revision=0,
        selections=(selection,),
    )
    assert created.revision == 1
    assert (await service.get_profile(scenario.actor, session_value.session_id)) == created

    with pytest.raises(McpProfileRevisionConflictError):
        await service.update_profile(
            scenario.actor,
            session_value.session_id,
            expected_revision=0,
            selections=(),
        )
    assert (await service.get_profile(scenario.actor, session_value.session_id)).revision == 1
    with pytest.raises(AgentSessionNotFoundError):
        await service.get_profile(ActorContext(owner_id="other"), session_value.session_id)


@pytest.mark.asyncio
async def test_profile_rejects_two_versions_without_creating_revision(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    session_value = await make_agent_service(scenario.factory).create_session(
        scenario.actor, scenario.project.project_id, title="MCP duplicate"
    )
    service = McpConfigurationService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        profile_repo_factory=SqlalchemyMcpProfileRepository,
        catalog=_catalog_with_two_versions(),
    )
    created = await service.update_profile(
        scenario.actor,
        session_value.session_id,
        expected_revision=0,
        selections=(McpProfileSelection("fixture-search", "1.0.0", (("corpus", "papers"),)),),
    )

    with pytest.raises(McpProfileInvalidError):
        await service.update_profile(
            scenario.actor,
            session_value.session_id,
            expected_revision=1,
            selections=(
                McpProfileSelection("fixture-search", "1.0.0", (("corpus", "papers"),)),
                McpProfileSelection("fixture-search", "2.0.0", (("corpus", "papers"),)),
            ),
        )

    current = await service.get_profile(scenario.actor, session_value.session_id)
    assert current.revision == created.revision == 1
    assert current.selections == created.selections


@pytest.mark.asyncio
async def test_turn_freezes_profile_version_config_hash_and_tool_schema(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    catalog = _catalog()
    agent_service = make_agent_service(scenario.factory, mcp_catalog=catalog)
    session_value = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title="MCP snapshot"
    )
    profile_service = McpConfigurationService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        profile_repo_factory=SqlalchemyMcpProfileRepository,
        catalog=catalog,
    )
    profile = await profile_service.update_profile(
        scenario.actor,
        session_value.session_id,
        expected_revision=0,
        selections=(McpProfileSelection("fixture-search", "1.0.0", (("corpus", "papers"),)),),
    )

    posted = await agent_service.post_message(
        scenario.actor,
        session_value.session_id,
        content="使用检索工具",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="mcp-turn",
        correlation_id="mcp-turn",
    )
    turn = await agent_service.get_turn(scenario.actor, posted.run_id)

    assert turn.policy_snapshot.policy_version.endswith("-mcp.v1")
    profile_id = turn.policy_snapshot.mcp_refs[0].profile_id
    assert profile_id
    assert turn.policy_snapshot.mcp_refs[0].profile_revision == 1
    assert turn.policy_snapshot.mcp_refs[0].catalog_id == "fixture-search"
    assert turn.policy_snapshot.mcp_refs[0].version == "1.0.0"
    assert turn.policy_snapshot.mcp_refs[0].config_hash == profile.selections[0].config_hash
    assert turn.policy_snapshot.mcp_refs[0].tools[0].name == "fixture-search_search"
    assert "fixture-search_search" in turn.policy_snapshot.allowed_tool_names

    updated = await profile_service.update_profile(
        scenario.actor,
        session_value.session_id,
        expected_revision=1,
        selections=(),
    )
    assert updated.revision == 2
    reloaded_turn = await agent_service.get_turn(scenario.actor, posted.run_id)
    assert reloaded_turn.policy_snapshot.mcp_refs == turn.policy_snapshot.mcp_refs
    async with scenario.factory() as session:
        repo = SqlalchemyMcpProfileRepository(session)
        frozen = await repo.get_revision_scoped(
            profile_id,
            1,
            session_value.session_id,
            scenario.actor.owner_id,
        )
        wrong_owner = await repo.get_revision_scoped(
            profile_id, 1, session_value.session_id, "other"
        )
        wrong_session = await repo.get_revision_scoped(
            profile_id, 1, "other-session", scenario.actor.owner_id
        )
    assert frozen is not None
    assert frozen.selections == profile.selections
    assert catalog.resolve_profile(frozen) == turn.policy_snapshot.mcp_refs
    assert wrong_owner is None and wrong_session is None


@pytest.mark.asyncio
async def test_tool_execution_is_replayed_and_events_exclude_arguments_and_result(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    catalog = _catalog()
    agent_service = make_agent_service(scenario.factory, mcp_catalog=catalog)
    session_value = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title="MCP effects"
    )
    profile_service = McpConfigurationService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        profile_repo_factory=SqlalchemyMcpProfileRepository,
        catalog=catalog,
    )
    await profile_service.update_profile(
        scenario.actor,
        session_value.session_id,
        expected_revision=0,
        selections=(McpProfileSelection("fixture-search", "1.0.0", (("corpus", "papers"),)),),
    )
    posted = await agent_service.post_message(
        scenario.actor,
        session_value.session_id,
        content="使用检索工具",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="mcp-effect-turn",
        correlation_id="mcp-effect-turn",
    )
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        run = await run_repo.get_by_id(posted.run_id)
        assert run is not None
        assert await run_repo.update_status(
            run.run_id, RunStatus.QUEUED, RunStatus.RUNNING, run.event_sequence
        )
        await session.commit()

    service = McpToolExecutionService(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )
    arguments = {
        "query": "private query",
        "endpoint": "https://must-not-enter-event.example",
        "token": "must-not-enter-event",
    }
    result = {"content": [{"type": "text", "text": "large private result"}]}
    assert (
        await service.begin(
            posted.run_id,
            "fixture-search_search",
            arguments,
            invocation_id="call-1-private",
        )
        is None
    )
    await service.succeed(
        posted.run_id,
        "fixture-search_search",
        arguments,
        result,
        invocation_id="call-1-private",
    )
    assert (
        await service.begin(
            posted.run_id,
            "fixture-search_search",
            arguments,
            invocation_id="call-1-private",
        )
        == result
    )
    assert (
        await service.begin(
            posted.run_id,
            "fixture-search_search",
            arguments,
            invocation_id="call-2-private",
        )
        is None
    )
    await service.succeed(
        posted.run_id,
        "fixture-search_search",
        arguments,
        result,
        invocation_id="call-2-private",
    )

    with pytest.raises(ResearchAgentRuntimeError) as changed_args:
        await service.begin(
            posted.run_id,
            "fixture-search_search",
            {"query": "changed"},
            invocation_id="call-1-private",
        )
    assert changed_args.value.code == "runtime_mcp_invocation_conflict"
    with pytest.raises(ResearchAgentRuntimeError) as changed_tool:
        await service.begin(
            posted.run_id,
            "fixture-search_lookup",
            arguments,
            invocation_id="call-1-private",
        )
    assert changed_tool.value.code == "runtime_mcp_invocation_conflict"

    for index in range(3, 13):
        assert (
            await service.begin(
                posted.run_id,
                "fixture-search_search",
                arguments,
                invocation_id=f"call-{index}-private",
            )
            is None
        )
    with pytest.raises(ResearchAgentRuntimeError) as budget:
        await service.begin(
            posted.run_id,
            "fixture-search_search",
            arguments,
            invocation_id="call-13-private",
        )
    assert budget.value.code == "runtime_mcp_tool_budget_exceeded"

    async with scenario.factory() as session:
        effects = await SqlalchemyToolExecutionRepository(session).list_by_turn(posted.run_id)
        events = await SqlalchemyEventRepository(session).list_by_run(posted.run_id)
    assert len(effects) == 12
    assert all(effect.attempt_count == 1 for effect in effects)
    assert len({effect.effect_id for effect in effects}) == 12
    assert len({effect.args_hash for effect in effects}) == 12
    tool_events = [event for event in events if event.event_type.startswith("agent_tool_")]
    assert sum(event.event_type == "agent_tool_started" for event in tool_events) == 12
    assert sum(event.event_type == "agent_tool_completed" for event in tool_events) == 2
    event_text = json.dumps(
        [event.payload for event in tool_events], ensure_ascii=False, sort_keys=True
    )
    for excluded in (
        "private query",
        "must-not-enter-event",
        "https://must-not-enter-event.example",
        "large private result",
        "call-1-private",
        "call-2-private",
    ):
        assert excluded not in event_text
        assert all(excluded not in effect.effect_id for effect in effects)
