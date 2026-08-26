"""Sandbox Lease/WorkspaceSnapshot PostgreSQL CAS 集成测试。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlService,
)
from literature_agent.domain.run import RunStatus
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt
from literature_agent.domain.workspace_snapshot import (
    WorkspaceFile,
    create_workspace_snapshot,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseRecord,
    SandboxLeaseStatus,
)
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from literature_agent.infrastructure.persistence.runtime_execution_repository import (
    SqlalchemyRuntimeExecutionRepository,
)
from literature_agent.infrastructure.persistence.sandbox_workspace_repository import (
    SqlalchemySandboxWorkspaceRepository,
    SqlalchemyWorkspaceSnapshotPublisher,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario


async def test_lease_fence_and_stable_snapshot_are_persisted_atomically(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="Sandbox repository"
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="创建工作区快照",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="sandbox-repository",
        correlation_id="sandbox-repository",
    )
    repository = SqlalchemySandboxWorkspaceRepository(scenario.factory)
    now = datetime.now(UTC)
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        run = await run_repo.get_by_id(submitted.run_id)
        assert run is not None
        assert await run_repo.update_status(
            run.run_id, RunStatus.QUEUED, RunStatus.RUNNING, run.event_sequence
        )
        await SqlalchemyAttemptRepository(session).add(
            RunAttempt(
                attempt_id="sandbox-attempt-1",
                run_id=run.run_id,
                attempt_number=1,
                worker_id="worker-1",
                status=AttemptStatus.RUNNING,
                started_at=now,
                heartbeat_at=now,
            )
        )
        await session.commit()
    control = RuntimeExecutionControlService(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        execution_repo_factory=SqlalchemyRuntimeExecutionRepository,
        lease_seconds=30,
        clock=lambda: now,
    )
    execution = await control.claim(
        turn_run_id=submitted.run_id,
        session_id=agent_session.session_id,
        runtime_execution_id="sandbox-runtime-execution-1",
        request_hash="a" * 64,
        owner_id="sandbox-runtime-owner-1",
    )
    async with scenario.factory() as session:
        publisher = SqlalchemyWorkspaceSnapshotPublisher(session)
        assert await publisher.publish_for_success(
            owner_id=scenario.actor.owner_id,
            project_id=scenario.project.project_id,
            session_id=agent_session.session_id,
            turn_run_id=submitted.run_id,
            required=False,
        )
        assert not await publisher.publish_for_success(
            owner_id=scenario.actor.owner_id,
            project_id=scenario.project.project_id,
            session_id=agent_session.session_id,
            turn_run_id=submitted.run_id,
            required=True,
        )
    lease = SandboxLeaseRecord(
        session_id=agent_session.session_id,
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        holder_turn_run_id=submitted.run_id,
        sandbox_id="sandbox-repository-1",
        image_ref="research-agent@sha256:pinned",
        generation=1,
        fencing_token=1,
        status=SandboxLeaseStatus.ACTIVE,
        generation_started_at=now,
        expires_at=now + timedelta(minutes=10),
        updated_at=now,
    )

    assert await repository.replace_lease(lease, expected_fencing_token=None)
    assert not await repository.replace_lease(
        replace(lease, fencing_token=2), expected_fencing_token=99
    )
    fenced = replace(lease, fencing_token=2, updated_at=now + timedelta(seconds=1))
    assert await repository.replace_lease(fenced, expected_fencing_token=1)

    file = WorkspaceFile(
        path="/workspace/notes.md",
        content_hash="a" * 64,
        size_bytes=6,
    )
    snapshot = create_workspace_snapshot(
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        session_id=agent_session.session_id,
        turn_run_id=submitted.run_id,
        version=1,
        sandbox_generation=1,
        files=(file,),
    )
    assert not await repository.stage_snapshot(
        snapshot, lease_generation=1, fencing_token=1
    )
    assert await repository.stage_snapshot(
        snapshot, lease_generation=1, fencing_token=2
    )
    assert not await repository.stage_snapshot(
        snapshot, lease_generation=1, fencing_token=2
    )

    assert await repository.latest_snapshot(agent_session.session_id) is None
    assert await repository.snapshot_for_turn(submitted.run_id) == snapshot
    async with scenario.factory() as session:
        publisher = SqlalchemyWorkspaceSnapshotPublisher(session)
        assert not await publisher.publish_for_success(
            owner_id=scenario.actor.owner_id,
            project_id=scenario.project.project_id,
            session_id=agent_session.session_id,
            turn_run_id=submitted.run_id,
            required=True,
        )
        await session.rollback()
    await control.succeed(execution.permit, "checkpoint-final")
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        current = await run_repo.get_by_id(submitted.run_id)
        assert current is not None
        assert await run_repo.update_status(
            submitted.run_id,
            RunStatus.RUNNING,
            RunStatus.CANCEL_REQUESTED,
            current.event_sequence,
        )
        publisher = SqlalchemyWorkspaceSnapshotPublisher(session)
        assert not await publisher.publish_for_success(
            owner_id=scenario.actor.owner_id,
            project_id=scenario.project.project_id,
            session_id=agent_session.session_id,
            turn_run_id=submitted.run_id,
            required=True,
        )
        await session.rollback()
    assert await repository.latest_snapshot(agent_session.session_id) is None
    async with scenario.factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        current = await run_repo.get_by_id(submitted.run_id)
        assert current is not None
        assert await run_repo.update_status(
            submitted.run_id,
            RunStatus.RUNNING,
            RunStatus.SUCCEEDED,
            current.event_sequence,
        )
        publisher = SqlalchemyWorkspaceSnapshotPublisher(session)
        assert await publisher.publish_for_success(
            owner_id=scenario.actor.owner_id,
            project_id=scenario.project.project_id,
            session_id=agent_session.session_id,
            turn_run_id=submitted.run_id,
            required=True,
        )
        await session.commit()
    stable = await repository.snapshot_for_turn(submitted.run_id)
    assert stable is not None
    assert stable.status.value == "stable"
    assert await repository.latest_snapshot(agent_session.session_id) == stable
    assert not await repository.mark_dirty(agent_session.session_id, 1, 1)
    assert await repository.mark_dirty(agent_session.session_id, 1, 2)
    persisted = await repository.get_lease(agent_session.session_id)
    assert persisted is not None
    assert persisted.status is SandboxLeaseStatus.DIRTY
