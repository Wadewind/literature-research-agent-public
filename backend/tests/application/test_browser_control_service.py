import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from literature_agent.application.browser_control_service import BrowserControlService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.browser_control import BrowserControlStatus, browser_ticket_digest
from literature_agent.domain.exceptions import (
    AgentBrowserControlBusyError,
    AgentSessionBusyError,
    BrowserControlConflictError,
    BrowserControlNotFoundError,
)
from literature_agent.infrastructure.agent.browser_ticket import HmacBrowserTicketIssuer
from literature_agent.infrastructure.persistence.agent_repository import SqlalchemyAgentRepository
from literature_agent.infrastructure.persistence.browser_control_repository import (
    SqlalchemyBrowserControlRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.models import (
    AgentBrowserControlLeaseORM,
    AgentSandboxLeaseORM,
    RunORM,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _service(factory, *, now=NOW) -> BrowserControlService:
    return BrowserControlService(
        session_factory=factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        browser_repo_factory=SqlalchemyBrowserControlRepository,
        project_repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
        ticket_issuer=HmacBrowserTicketIssuer(b"b" * 32),
        clock=lambda: now,
    )


async def _prepare_idle_sandbox(scenario, *, fence: int = 7):
    agent = make_agent_service(scenario.factory)
    session_value = await agent.create_session(
        scenario.actor, scenario.project.project_id, title="Browser"
    )
    posted = await agent.post_message(
        scenario.actor,
        session_value.session_id,
        content="打开合成页面",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="browser-anchor",
        correlation_id="browser-anchor",
    )
    async with scenario.factory() as db:
        await db.execute(
            update(RunORM)
            .where(RunORM.run_id == posted.run_id)
            .values(status="succeeded")
        )
        await SqlalchemyAgentRepository(db).release_active_turn(
            session_value.session_id, posted.run_id
        )
        db.add(
            AgentSandboxLeaseORM(
                session_id=session_value.session_id,
                owner_id=scenario.actor.owner_id,
                project_id=scenario.project.project_id,
                holder_turn_run_id=posted.run_id,
                sandbox_id=f"sandbox-{session_value.session_id}",
                image_ref="fixed-image",
                generation=3,
                fencing_token=fence,
                status="active",
                generation_started_at=NOW - timedelta(minutes=1),
                expires_at=NOW + timedelta(minutes=10),
                updated_at=NOW,
            )
        )
        await db.commit()
    return agent, session_value, posted.run_id


@pytest.mark.asyncio
async def test_start_is_idempotent_scoped_and_persists_only_ticket_digest(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    _, session_value, anchor_run_id = await _prepare_idle_sandbox(scenario)
    service = _service(scenario.factory)

    first = await service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-start"
    )
    replay = await service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-replay"
    )

    assert replay == first
    assert first.view_url == "/api/v1/agent-browser-controls/view"
    assert "sandbox" not in first.view_url
    async with scenario.factory() as db:
        row = (
            await db.execute(select(AgentBrowserControlLeaseORM))
        ).scalar_one()
        assert row.ticket_digest == browser_ticket_digest(first.ticket)
        assert first.ticket not in row.ticket_digest
        events = await SqlalchemyEventRepository(db).list_by_run(anchor_run_id)
        event = [e for e in events if e.event_type == "agent_browser_control_started"]
        assert len(event) == 1
        assert set(event[0].payload) == {
            "session_id",
            "sandbox_generation",
            "revision",
        }


@pytest.mark.asyncio
async def test_ticket_key_change_rotates_control_and_rejects_old_ticket(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    _, session_value, _ = await _prepare_idle_sandbox(scenario)
    first_service = _service(scenario.factory)
    first = await first_service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-start"
    )
    rotated_service = BrowserControlService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        browser_repo_factory=SqlalchemyBrowserControlRepository,
        project_repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
        ticket_issuer=HmacBrowserTicketIssuer(b"c" * 32),
        clock=lambda: NOW,
    )

    rotated = await rotated_service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-restart"
    )

    assert rotated.control.revision == first.control.revision + 1
    assert rotated.ticket != first.ticket
    with pytest.raises(BrowserControlNotFoundError):
        await rotated_service.claim_view(
            scenario.actor, first.ticket, connection_id="old-ticket"
        )


@pytest.mark.asyncio
async def test_manual_control_blocks_turn_until_idempotent_end(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent, session_value, _ = await _prepare_idle_sandbox(scenario)
    service = _service(scenario.factory)
    await service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-start"
    )

    with pytest.raises(AgentBrowserControlBusyError):
        await agent.post_message(
            scenario.actor,
            session_value.session_id,
            content="不应与人工控制并发",
            review_output_id=scenario.matrix.output_id,
            idempotency_key="blocked-by-browser",
            correlation_id="blocked-by-browser",
        )

    ended = await service.end(
        scenario.actor, session_value.session_id, correlation_id="browser-end"
    )
    replay = await service.end(
        scenario.actor, session_value.session_id, correlation_id="browser-end-replay"
    )
    assert ended == replay
    assert ended.status == BrowserControlStatus.ENDED.value
    posted = await agent.post_message(
        scenario.actor,
        session_value.session_id,
        content="人工操作后继续",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="after-browser",
        correlation_id="after-browser",
    )
    assert posted.status == "queued"


@pytest.mark.asyncio
async def test_active_turn_rejects_manual_start(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent = make_agent_service(scenario.factory)
    value = await agent.create_session(scenario.actor, scenario.project.project_id, title=None)
    await agent.post_message(
        scenario.actor,
        value.session_id,
        content="仍在运行",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="running-turn",
        correlation_id="running-turn",
    )
    with pytest.raises(AgentSessionBusyError):
        await _service(scenario.factory).start(
            scenario.actor, value.session_id, correlation_id="browser-start"
        )


@pytest.mark.asyncio
async def test_one_viewer_and_generation_fence_invalidate_replayed_ticket(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    _, session_value, _ = await _prepare_idle_sandbox(scenario)
    service = _service(scenario.factory)
    started = await service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-start"
    )
    first = await service.claim_view(
        scenario.actor, started.ticket, connection_id="connection-1"
    )
    with pytest.raises(BrowserControlConflictError):
        await service.claim_view(
            scenario.actor, started.ticket, connection_id="connection-2"
        )
    await service.release_view(first)
    second = await service.claim_view(
        scenario.actor, started.ticket, connection_id="connection-2"
    )
    assert await service.view_is_current(second)

    async with scenario.factory() as db:
        await db.execute(
            update(AgentSandboxLeaseORM)
            .where(AgentSandboxLeaseORM.session_id == session_value.session_id)
            .values(fencing_token=8)
        )
        await db.commit()
    assert not await service.view_is_current(second)
    await service.release_view(second)
    with pytest.raises(BrowserControlNotFoundError):
        await service.claim_view(
            scenario.actor, started.ticket, connection_id="connection-3"
        )


@pytest.mark.asyncio
async def test_concurrent_view_claim_has_exactly_one_controller(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    _, session_value, _ = await _prepare_idle_sandbox(scenario)
    service = _service(scenario.factory)
    started = await service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-start"
    )

    results = await asyncio.gather(
        service.claim_view(
            scenario.actor, started.ticket, connection_id="connection-a"
        ),
        service.claim_view(
            scenario.actor, started.ticket, connection_id="connection-b"
        ),
        return_exceptions=True,
    )

    grants = [value for value in results if not isinstance(value, Exception)]
    conflicts = [
        value for value in results if isinstance(value, BrowserControlConflictError)
    ]
    assert len(grants) == 1
    assert len(conflicts) == 1


@pytest.mark.asyncio
async def test_cross_owner_is_hidden_and_expiry_reconciles(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    _, session_value, _ = await _prepare_idle_sandbox(scenario)
    service = _service(scenario.factory)
    started = await service.start(
        scenario.actor, session_value.session_id, correlation_id="browser-start"
    )
    with pytest.raises(BrowserControlNotFoundError):
        await service.claim_view(
            ActorContext(owner_id="other-owner"),
            started.ticket,
            connection_id="foreign",
        )
    with pytest.raises(BrowserControlNotFoundError):
        await service.claim_view(
            scenario.actor,
            "非 ASCII ticket",
            connection_id="malformed",
        )
    later = _service(scenario.factory, now=NOW + timedelta(minutes=6))
    expired = await later.get(
        scenario.actor, session_value.session_id, correlation_id="browser-expire"
    )
    assert expired is not None
    assert expired.status == BrowserControlStatus.EXPIRED.value
