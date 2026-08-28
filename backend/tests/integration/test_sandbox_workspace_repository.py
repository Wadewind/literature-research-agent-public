"""Sandbox Lease/WorkspaceSnapshot PostgreSQL CAS 集成测试。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlService,
)
from literature_agent.domain.run import RunStatus
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt
from literature_agent.domain.workspace_snapshot import (
    WorkspaceFile,
    create_workspace_snapshot,
)
from literature_agent.infrastructure.agent.sandbox_cleanup import (
    SandboxCleanupService,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxCleanupStatus,
    SandboxLeaseRecord,
    SandboxLeaseStatus,
    create_sandbox_cleanup_task,
)
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.models import (
    AgentSandboxCleanupORM,
    AgentSandboxLeaseORM,
    AgentSessionORM,
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


async def test_cleanup_retirement_is_fenced_and_retry_is_effectively_once(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="Sandbox cleanup"
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="建立 Sandbox 清理测试",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="sandbox-cleanup-repository",
        correlation_id="sandbox-cleanup-repository",
    )
    repository = SqlalchemySandboxWorkspaceRepository(scenario.factory)
    now = datetime.now(UTC)
    original = SandboxLeaseRecord(
        session_id=agent_session.session_id,
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        holder_turn_run_id=submitted.run_id,
        sandbox_id="sandbox-cleanup-private-resource",
        image_ref="research-agent@sha256:pinned",
        generation=1,
        fencing_token=1,
        status=SandboxLeaseStatus.ACTIVE,
        generation_started_at=now,
        expires_at=now + timedelta(seconds=5),
        updated_at=now,
    )
    assert await repository.replace_lease(original, expected_fencing_token=None)

    renewed = replace(
        original,
        fencing_token=2,
        expires_at=now + timedelta(minutes=5),
        updated_at=now + timedelta(seconds=1),
    )
    assert await repository.replace_lease(renewed, expected_fencing_token=1)
    assert await repository.enqueue_due_cleanups(
        now=now + timedelta(seconds=10), limit=10
    ) == 0
    assert await repository.mark_dirty(agent_session.session_id, 1, 2)
    assert await repository.enqueue_due_cleanups(
        now=now + timedelta(seconds=11), limit=10
    ) == 1
    retired = await repository.get_lease(agent_session.session_id)
    assert retired is not None
    assert retired.status is SandboxLeaseStatus.RETIRED
    assert not await repository.replace_lease(
        replace(
            renewed,
            fencing_token=3,
            updated_at=now + timedelta(seconds=12),
        ),
        expected_fencing_token=2,
    )
    next_generation = replace(
        renewed,
        sandbox_id="sandbox-cleanup-next-generation",
        generation=2,
        fencing_token=3,
        updated_at=now + timedelta(seconds=12),
    )
    assert await repository.replace_lease(
        next_generation,
        expected_fencing_token=2,
    )

    first_claim = await repository.claim_cleanup_tasks(
        worker_id="cleaner-1",
        now=now + timedelta(seconds=11),
        lease_seconds=30,
        limit=10,
    )
    assert len(first_claim) == 1
    task = first_claim[0]
    assert task.status is SandboxCleanupStatus.RUNNING
    assert task.reason == "dirty"
    assert task.attempt_count == 1
    assert not await repository.claim_cleanup_tasks(
        worker_id="cleaner-2",
        now=now + timedelta(seconds=12),
        lease_seconds=30,
        limit=10,
    )
    assert await repository.retry_cleanup(
        task.cleanup_id,
        worker_id="cleaner-1",
        attempt_count=1,
        next_attempt_at=now + timedelta(seconds=20),
        error_code="provider_destroy_failed",
        error_summary="远端 Sandbox 销毁暂时失败",
        now=now + timedelta(seconds=12),
    )
    assert not await repository.claim_cleanup_tasks(
        worker_id="cleaner-2",
        now=now + timedelta(seconds=19),
        lease_seconds=30,
        limit=10,
    )
    second_claim = await repository.claim_cleanup_tasks(
        worker_id="cleaner-2",
        now=now + timedelta(seconds=20),
        lease_seconds=30,
        limit=10,
    )
    assert len(second_claim) == 1
    assert second_claim[0].attempt_count == 2
    assert not await repository.complete_cleanup(
        task.cleanup_id,
        worker_id="cleaner-1",
        attempt_count=1,
        now=now + timedelta(seconds=21),
    )
    assert await repository.complete_cleanup(
        task.cleanup_id,
        worker_id="cleaner-2",
        attempt_count=2,
        now=now + timedelta(seconds=21),
    )


async def test_expired_and_closed_leases_retire_atomically_before_provider_destroy(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    expired_session = await agent_service.create_session(
        scenario.actor,
        scenario.project.project_id,
        title="Expired Sandbox cleanup",
    )
    closed_session = await agent_service.create_session(
        scenario.actor,
        scenario.project.project_id,
        title="Closed Session Sandbox cleanup",
    )
    expired_turn = await agent_service.post_message(
        scenario.actor,
        expired_session.session_id,
        content="建立过期 Sandbox 清理测试",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="sandbox-cleanup-expired",
        correlation_id="sandbox-cleanup-expired",
    )
    closed_turn = await agent_service.post_message(
        scenario.actor,
        closed_session.session_id,
        content="建立关闭 Session 清理测试",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="sandbox-cleanup-session-closed",
        correlation_id="sandbox-cleanup-session-closed",
    )
    repository = SqlalchemySandboxWorkspaceRepository(scenario.factory)
    now = datetime.now(UTC)
    expired_lease = SandboxLeaseRecord(
        session_id=expired_session.session_id,
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        holder_turn_run_id=expired_turn.run_id,
        sandbox_id="sandbox-expired-private-resource",
        image_ref="research-agent@sha256:pinned",
        generation=1,
        fencing_token=1,
        status=SandboxLeaseStatus.ACTIVE,
        generation_started_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
        updated_at=now - timedelta(minutes=10),
    )
    closed_lease = SandboxLeaseRecord(
        session_id=closed_session.session_id,
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        holder_turn_run_id=closed_turn.run_id,
        sandbox_id="sandbox-closed-private-resource",
        image_ref="research-agent@sha256:pinned",
        generation=1,
        fencing_token=1,
        status=SandboxLeaseStatus.ACTIVE,
        generation_started_at=now,
        expires_at=now + timedelta(minutes=10),
        updated_at=now,
    )
    assert await repository.replace_lease(expired_lease, expected_fencing_token=None)
    assert await repository.replace_lease(closed_lease, expected_fencing_token=None)
    async with scenario.factory() as session:
        row = await session.get(AgentSessionORM, closed_session.session_id)
        assert row is not None
        row.status = "closed"
        await session.commit()

    # 一个 Repository 调用先以 generation/fence/due guard 退役两条 Lease，
    # 并在同一提交中建立稳定 cleanup fact；此处尚无 Provider 参与。
    assert await repository.enqueue_due_cleanups(now=now, limit=10) == 2
    expected_expired = create_sandbox_cleanup_task(
        owner_id=expired_lease.owner_id,
        project_id=expired_lease.project_id,
        session_id=expired_lease.session_id,
        sandbox_id=expired_lease.sandbox_id,
        generation=1,
        fencing_token=1,
        reason="expired",
        now=now,
    )
    expected_closed = create_sandbox_cleanup_task(
        owner_id=closed_lease.owner_id,
        project_id=closed_lease.project_id,
        session_id=closed_lease.session_id,
        sandbox_id=closed_lease.sandbox_id,
        generation=1,
        fencing_token=1,
        reason="session_closed",
        now=now,
    )
    async with scenario.factory() as session:
        leases = (
            await session.execute(
                select(AgentSandboxLeaseORM).where(
                    AgentSandboxLeaseORM.session_id.in_(
                        [expired_session.session_id, closed_session.session_id]
                    )
                )
            )
        ).scalars().all()
        cleanups = (
            await session.execute(
                select(AgentSandboxCleanupORM).where(
                    AgentSandboxCleanupORM.cleanup_id.in_(
                        [expected_expired.cleanup_id, expected_closed.cleanup_id]
                    )
                )
            )
        ).scalars().all()
    assert {row.status for row in leases} == {SandboxLeaseStatus.RETIRED.value}
    assert {
        (row.cleanup_id, row.reason, row.generation, row.fencing_token, row.status)
        for row in cleanups
    } == {
        (
            expected_expired.cleanup_id,
            "expired",
            1,
            1,
            SandboxCleanupStatus.PENDING.value,
        ),
        (
            expected_closed.cleanup_id,
            "session_closed",
            1,
            1,
            SandboxCleanupStatus.PENDING.value,
        ),
    }

    # 新 generation 只有在旧 generation 已退役后才提交；旧 fence 不能再标脏或覆盖它。
    next_generation = replace(
        expired_lease,
        sandbox_id="sandbox-expired-next-generation",
        generation=2,
        fencing_token=2,
        status=SandboxLeaseStatus.ACTIVE,
        generation_started_at=now + timedelta(seconds=1),
        expires_at=now + timedelta(minutes=10),
        updated_at=now + timedelta(seconds=1),
    )
    assert await repository.replace_lease(
        next_generation,
        expected_fencing_token=1,
    )
    assert not await repository.mark_dirty(expired_session.session_id, 1, 1)
    assert not await repository.replace_lease(
        replace(expired_lease, fencing_token=2, updated_at=now + timedelta(seconds=2)),
        expected_fencing_token=1,
    )

    class _CommittedStateProvider:
        def __init__(self) -> None:
            self.destroyed: list[str] = []

        async def destroy(self, sandbox_id: str) -> None:
            # 能从独立连接看到 RUNNING cleanup 与新 Lease，证明调度/认领事务已在
            # 外部 Provider I/O 前提交；Provider 不接收或持有数据库 Session。
            async with scenario.factory() as session:
                cleanup = (
                    await session.execute(
                        select(AgentSandboxCleanupORM).where(
                            AgentSandboxCleanupORM.sandbox_id == sandbox_id
                        )
                    )
                ).scalar_one()
                lease = await session.get(AgentSandboxLeaseORM, cleanup.session_id)
                assert cleanup.status == SandboxCleanupStatus.RUNNING.value
                assert lease is not None
                if cleanup.session_id == expired_session.session_id:
                    assert lease.generation == 2
                    assert lease.fencing_token == 2
                    assert lease.sandbox_id == next_generation.sandbox_id
                else:
                    assert lease.status == SandboxLeaseStatus.RETIRED.value
            self.destroyed.append(sandbox_id)

    provider = _CommittedStateProvider()
    cleanup_service = SandboxCleanupService(
        repository=repository,
        provider=provider,
        worker_id="cleanup-integration-worker",
        clock=lambda: now + timedelta(seconds=2),
    )
    result = await cleanup_service.run_once()
    assert result.scheduled == 0
    assert result.claimed == 2
    assert result.succeeded == 2
    assert result.retried == 0
    assert set(provider.destroyed) == {
        expired_lease.sandbox_id,
        closed_lease.sandbox_id,
    }
    assert await repository.get_lease(expired_session.session_id) == next_generation
    async with scenario.factory() as session:
        statuses = (
            await session.execute(
                select(AgentSandboxCleanupORM.status).where(
                    AgentSandboxCleanupORM.cleanup_id.in_(
                        [expected_expired.cleanup_id, expected_closed.cleanup_id]
                    )
                )
            )
        ).scalars().all()
    assert statuses == [
        SandboxCleanupStatus.SUCCEEDED.value,
        SandboxCleanupStatus.SUCCEEDED.value,
    ]


async def test_rotation_persists_cleanup_in_same_lease_transaction(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title="Atomic cleanup"
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="建立轮换清理测试",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="sandbox-cleanup-rotation",
        correlation_id="sandbox-cleanup-rotation",
    )
    repository = SqlalchemySandboxWorkspaceRepository(scenario.factory)
    now = datetime.now(UTC)
    old = SandboxLeaseRecord(
        session_id=agent_session.session_id,
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        holder_turn_run_id=submitted.run_id,
        sandbox_id="sandbox-rotation-old-private",
        image_ref="research-agent@sha256:old",
        generation=1,
        fencing_token=1,
        status=SandboxLeaseStatus.DIRTY,
        generation_started_at=now,
        expires_at=now + timedelta(minutes=5),
        updated_at=now,
    )
    assert await repository.replace_lease(old, expected_fencing_token=None)
    cleanup = create_sandbox_cleanup_task(
        owner_id=old.owner_id,
        project_id=old.project_id,
        session_id=old.session_id,
        sandbox_id=old.sandbox_id,
        generation=old.generation,
        fencing_token=old.fencing_token,
        reason="rotation",
        now=now + timedelta(seconds=1),
    )
    current = replace(
        old,
        sandbox_id="sandbox-rotation-new-private",
        image_ref="research-agent@sha256:new",
        generation=2,
        fencing_token=2,
        status=SandboxLeaseStatus.ACTIVE,
        updated_at=now + timedelta(seconds=1),
    )
    assert await repository.replace_lease(
        current,
        expected_fencing_token=1,
        cleanup_replaced=cleanup,
    )
    claimed = await repository.claim_cleanup_tasks(
        worker_id="cleaner-rotation",
        now=now + timedelta(seconds=1),
        lease_seconds=30,
        limit=10,
    )
    assert len(claimed) == 1
    assert claimed[0].cleanup_id == cleanup.cleanup_id
    assert claimed[0].sandbox_id == old.sandbox_id
