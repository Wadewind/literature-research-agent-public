"""AgentSessionService 的真实行为与原子 bundle 测试。"""

import pytest
from sqlalchemy import update

from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    AgentReviewOutputNotFoundError,
    AgentSessionBusyError,
    AgentTurnNotFoundError,
    ProjectNotFoundError,
)
from literature_agent.domain.project import create_project
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.models import AgentTurnUsageORM
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine


@pytest.mark.asyncio
async def test_list_sessions_requires_owned_project_and_returns_project_scope(db_engine) -> None:
    """Application 先校验 Project，再只返回该 Project 的 Session。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    created = await service.create_session(
        scenario.actor, scenario.project.project_id, title="研究会话"
    )

    listed = await service.list_sessions(scenario.actor, scenario.project.project_id)

    assert [value.session_id for value in listed] == [created.session_id]
    with pytest.raises(ProjectNotFoundError):
        await service.list_sessions(
            ActorContext(owner_id="other-owner"), scenario.project.project_id
        )
    with pytest.raises(ProjectNotFoundError):
        await service.list_sessions(scenario.actor, "missing-project")


@pytest.mark.asyncio
async def test_project_context_summary_counts_current_ready_indexes_and_checks_owner(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)

    assert await service.get_project_ready_index_count(
        scenario.actor, scenario.project.project_id
    ) == 1
    with pytest.raises(ProjectNotFoundError):
        await service.get_project_ready_index_count(
            ActorContext(owner_id="other-owner"), scenario.project.project_id
        )


@pytest.mark.asyncio
async def test_post_message_commits_one_scoped_bundle_and_replays_idempotently(
    db_engine,
) -> None:
    """消息、Run、Snapshot、Event、Outbox 必须同事务出现且同键不重复。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="应用行为"
    )

    first = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="分析第一轮",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="application-turn-1",
        correlation_id="application-1",
    )
    replay = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="分析第一轮",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="application-turn-1",
        correlation_id="application-replay",
    )

    assert replay == first
    with pytest.raises(AgentSessionBusyError):
        await service.post_message(
            scenario.actor,
            agent_session.session_id,
            content="不能并发第二轮",
            review_output_id=scenario.matrix.output_id,
            idempotency_key="application-turn-busy",
            correlation_id="application-busy",
        )
    async with scenario.factory() as session:
        agent_repo = SqlalchemyAgentRepository(session)
        messages = await agent_repo.list_messages_scoped(
            agent_session.session_id, scenario.actor.owner_id
        )
        turn = await agent_repo.get_turn_scoped(first.run_id, scenario.actor.owner_id)
        run = await SqlalchemyRunRepository(session).get_by_id(first.run_id)
        events = await SqlalchemyEventRepository(session).list_by_run(first.run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(first.run_id)
        assert len(messages) == 1
        assert turn is not None and run is not None and outbox is not None
        assert [event.event_type for event in events] == [
            "run_created",
            "agent_message_accepted",
        ]
        context = await agent_repo.get_context_snapshot(turn.context_snapshot_id)
        assert context is not None
        assert context.review_output_id == scenario.matrix.output_id
        assert context.project_index_refs[0].chunk_set_id == scenario.chunk_set_id
        policy = await agent_repo.get_policy_snapshot(turn.policy_snapshot_id)
        assert policy is not None
        assert policy.policy_version == "agent-policy.project-research-workspace.v5"
        assert policy.allowed_tool_names == (
            "search_project_chunks",
            "read_review_evidence_matrix",
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "execute",
            "submit_artifact",
        )
        assert policy.allowed_skill_names == ()
        assert policy.network_enabled is True
        assert policy.network_profile_id == "research-public-egress"
        assert policy.sandbox_enabled is True
        assert policy.approval_required is False
        assert policy.max_model_calls == 8
        assert policy.max_output_tokens_per_model_call == 4_096
        assert policy.max_tool_calls == 12


@pytest.mark.asyncio
async def test_post_message_rejects_unscoped_review_before_writing_bundle(db_engine) -> None:
    """不存在或越权 Matrix 不得留下 User Message、Run 或 active Turn。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )

    with pytest.raises(ValueError, match="Idempotency-Key"):
        await service.post_message(
            scenario.actor,
            agent_session.session_id,
            content="不会提交",
            review_output_id=scenario.matrix.output_id,
            idempotency_key="   ",
            correlation_id="application-blank-key",
        )

    with pytest.raises(AgentReviewOutputNotFoundError):
        await service.post_message(
            scenario.actor,
            agent_session.session_id,
            content="越权 Matrix",
            review_output_id="00000000-0000-0000-0000-000000000000",
            idempotency_key="application-bad-matrix",
            correlation_id="application-bad-matrix",
        )

    assert await service.list_messages(scenario.actor, agent_session.session_id) == []
    loaded = await service.get_session(scenario.actor, agent_session.session_id)
    assert loaded.active_turn_run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["owner", "project", "session", "policy"])
async def test_tool_execution_query_fails_closed_on_scope_or_policy_drift(
    db_engine, drift: str
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="Tool 摘要"
    )
    other_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="其他会话"
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="生成安全 Tool 摘要",
        review_output_id=scenario.matrix.output_id,
        idempotency_key=f"tool-execution-closure-{drift}",
        correlation_id=f"tool-execution-closure-{drift}",
    )

    view = await service.list_tool_executions(scenario.actor, submitted.run_id)
    assert view.usage.turn_run_id == submitted.run_id
    assert view.items == ()
    with pytest.raises(AgentTurnNotFoundError):
        await service.list_tool_executions(
            ActorContext(owner_id="other-owner"), submitted.run_id
        )

    async with scenario.factory() as session:
        values: dict[str, object]
        if drift == "owner":
            values = {"owner_id": "other-owner"}
        elif drift == "session":
            values = {"session_id": other_session.session_id}
        elif drift == "policy":
            values = {"max_tool_calls": 11}
        else:
            other_project = create_project(
                owner_id=scenario.actor.owner_id,
                name="其他 Project",
                description="",
            )
            await SqlalchemyProjectRepository(session).add(other_project)
            await session.flush()
            values = {"project_id": other_project.project_id}
        await session.execute(
            update(AgentTurnUsageORM)
            .where(AgentTurnUsageORM.turn_run_id == submitted.run_id)
            .values(**values)
        )
        await session.commit()

    with pytest.raises(AgentTurnNotFoundError):
        await service.list_tool_executions(scenario.actor, submitted.run_id)
