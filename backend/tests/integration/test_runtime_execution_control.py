"""Runtime Execution PostgreSQL lease/fencing 与跨进程恢复证据。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
    RuntimeExecutionControlService,
)
from literature_agent.domain.run import RunStatus
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from literature_agent.infrastructure.persistence.runtime_execution_repository import (
    SqlalchemyRuntimeExecutionRepository,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario


async def test_postgres_runtime_execution_allows_only_one_recovery_owner(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="Runtime lease"
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="恢复测试",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="runtime-lease",
        correlation_id="runtime-lease-correlation",
    )
    now = datetime.now(UTC)
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        run = await run_repo.get_by_id(submitted.run_id)
        assert run is not None
        assert await run_repo.update_status(
            run.run_id,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            run.event_sequence,
        )
        await SqlalchemyAttemptRepository(session).add(
            RunAttempt(
                attempt_id="runtime-attempt-1",
                run_id=run.run_id,
                attempt_number=1,
                worker_id="worker-1",
                status=AttemptStatus.RUNNING,
                started_at=now,
                heartbeat_at=now,
            )
        )
        await session.commit()

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    clock = [now]

    def control() -> RuntimeExecutionControlService:
        return RuntimeExecutionControlService(
            session_factory=factory,
            run_repo_factory=SqlalchemyRunRepository,
            attempt_repo_factory=SqlalchemyAttemptRepository,
            execution_repo_factory=SqlalchemyRuntimeExecutionRepository,
            lease_seconds=30,
            clock=lambda: clock[0],
        )

    first = await control().claim(
        turn_run_id=submitted.run_id,
        session_id=agent_session.session_id,
        runtime_execution_id="runtime-execution-1",
        request_hash="a" * 64,
        owner_id="runtime-owner-1",
    )
    async with factory() as session:
        attempts = SqlalchemyAttemptRepository(session)
        assert await attempts.finish_if_running(
            "runtime-attempt-1", AttemptStatus.FAILED, now + timedelta(seconds=31)
        )
        await attempts.add(
            RunAttempt(
                attempt_id="runtime-attempt-2",
                run_id=submitted.run_id,
                attempt_number=2,
                worker_id="worker-2",
                status=AttemptStatus.RUNNING,
                started_at=now + timedelta(seconds=31),
                heartbeat_at=now + timedelta(seconds=31),
            )
        )
        await session.commit()
    clock[0] = now + timedelta(seconds=31)

    results = await asyncio.gather(
        control().claim(
            turn_run_id=submitted.run_id,
            session_id=agent_session.session_id,
            runtime_execution_id="runtime-execution-1",
            request_hash="a" * 64,
            owner_id="runtime-owner-2",
        ),
        control().claim(
            turn_run_id=submitted.run_id,
            session_id=agent_session.session_id,
            runtime_execution_id="runtime-execution-1",
            request_hash="a" * 64,
            owner_id="runtime-owner-3",
        ),
        return_exceptions=True,
    )
    winners = [item for item in results if not isinstance(item, Exception)]
    losers = [item for item in results if isinstance(item, Exception)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], RuntimeExecutionControlError)
    assert winners[0].fencing_token == 2
    with pytest.raises(RuntimeExecutionControlError, match="lease"):
        await control().assert_active(first.permit)

    async with factory() as session:
        stored = await SqlalchemyRuntimeExecutionRepository(session).get(submitted.run_id)
    assert stored == winners[0]
