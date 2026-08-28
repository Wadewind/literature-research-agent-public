"""Agent Usage 在 PostgreSQL 上的并发 reservation 契约。"""

import asyncio

from literature_agent.application.agent_usage_service import AgentUsageService
from literature_agent.application.ports.agent_usage_control import (
    ToolCallReservationRequest,
)
from literature_agent.domain.run import RunStatus
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.agent_usage_repository import (
    SqlalchemyAgentUsageRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario


async def test_concurrent_replay_reserves_model_and_tool_only_once(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="并发预算"
    )
    posted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="并发重放",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="usage-concurrency",
        correlation_id="usage-concurrency",
    )
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        run = await run_repo.get_by_id_for_update(
            posted.run_id, scenario.actor.owner_id
        )
        assert run is not None
        assert await run_repo.update_status(
            run.run_id,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            run.event_sequence,
        )
        turn = await SqlalchemyAgentRepository(session).get_turn_scoped(
            run.run_id, scenario.actor.owner_id
        )
        assert turn is not None
        policy = await SqlalchemyAgentRepository(session).get_policy_snapshot(
            turn.policy_snapshot_id
        )
        assert policy is not None
        tool_ref = next(
            ref for ref in policy.tool_refs if ref.name == "search_project_chunks"
        )
        await session.commit()

    usage_service = AgentUsageService(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        usage_repo_factory=SqlalchemyAgentUsageRepository,
    )
    model_results = await asyncio.gather(
        *(
            usage_service.reserve_model_call(
                posted.run_id, 1, approximate_input_tokens=100
            )
            for _ in range(2)
        )
    )
    request = ToolCallReservationRequest(
        invocation_id="project-call-1",
        tool_name=tool_ref.name,
        input_schema_hash=tool_ref.input_schema_hash,
        args_hash="b" * 64,
        input_size_bytes=20,
    )
    tool_results = await asyncio.gather(
        *(usage_service.reserve_tool_call(posted.run_id, request) for _ in range(2))
    )

    assert {value.model_calls_reserved for value in model_results} == {1}
    assert tool_results[0] == tool_results[1]
    async with scenario.factory() as session:
        repo = SqlalchemyAgentUsageRepository(session)
        usage = await repo.get_usage(posted.run_id)
        assert usage is not None
        assert (usage.model_calls_reserved, usage.tool_calls_reserved) == (1, 1)
        assert len(await repo.list_tool_calls(posted.run_id)) == 1
