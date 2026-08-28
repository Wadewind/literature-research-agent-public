"""Sandbox Lease 与 WorkspaceSnapshot 的 PostgreSQL 短事务 Repository。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from literature_agent.domain.run import RunStatus
from literature_agent.domain.runtime_execution import RuntimeControlState
from literature_agent.domain.workspace_snapshot import (
    WorkspaceFile,
    WorkspaceSnapshot,
    WorkspaceSnapshotStatus,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxCleanupStatus,
    SandboxCleanupTask,
    SandboxLeaseRecord,
    SandboxLeaseStatus,
    SandboxWorkspaceRepository,
    create_sandbox_cleanup_task,
)
from literature_agent.infrastructure.persistence.models import (
    AgentRuntimeExecutionORM,
    AgentSandboxCleanupORM,
    AgentSandboxLeaseORM,
    AgentSessionORM,
    AgentWorkspaceSnapshotORM,
    RunORM,
)


class SqlalchemySandboxWorkspaceRepository(SandboxWorkspaceRepository):
    """每个方法自持一个短事务，避免 Provider/Storage I/O 落入事务。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[Any]],
    ) -> None:
        self._session_factory = session_factory

    async def get_lease(self, session_id: str) -> SandboxLeaseRecord | None:
        async with self._session_factory() as session:
            row = await session.get(AgentSandboxLeaseORM, session_id)
            return _lease(row) if row is not None else None

    async def replace_lease(
        self,
        value: SandboxLeaseRecord,
        *,
        expected_fencing_token: int | None,
        cleanup_replaced: SandboxCleanupTask | None = None,
    ) -> bool:
        async with self._session_factory() as session:
            if expected_fencing_token is None:
                result = await session.execute(
                    insert(AgentSandboxLeaseORM)
                    .values(**_lease_values(value))
                    .on_conflict_do_nothing(index_elements=[AgentSandboxLeaseORM.session_id])
                    .returning(AgentSandboxLeaseORM.session_id)
                )
            else:
                result = await session.execute(
                    update(AgentSandboxLeaseORM)
                    .where(
                        AgentSandboxLeaseORM.session_id == value.session_id,
                        AgentSandboxLeaseORM.fencing_token == expected_fencing_token,
                        or_(
                            and_(
                                AgentSandboxLeaseORM.generation == value.generation,
                                AgentSandboxLeaseORM.status
                                == SandboxLeaseStatus.ACTIVE.value,
                            ),
                            AgentSandboxLeaseORM.generation == value.generation - 1,
                        ),
                    )
                    .values(**_lease_values(value))
                    .returning(AgentSandboxLeaseORM.session_id)
                )
            changed = result.scalar_one_or_none() is not None
            if changed:
                if cleanup_replaced is not None:
                    await session.execute(
                        insert(AgentSandboxCleanupORM)
                        .values(**_cleanup_values(cleanup_replaced))
                        .on_conflict_do_nothing(
                            index_elements=[AgentSandboxCleanupORM.cleanup_id]
                        )
                    )
                await session.commit()
            return changed

    async def enqueue_cleanup(self, value: SandboxCleanupTask) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                insert(AgentSandboxCleanupORM)
                .values(**_cleanup_values(value))
                .on_conflict_do_nothing(
                    index_elements=[AgentSandboxCleanupORM.cleanup_id]
                )
                .returning(AgentSandboxCleanupORM.cleanup_id)
            )
            inserted = result.scalar_one_or_none() is not None
            if inserted:
                await session.commit()
            return inserted

    async def mark_cleanup_succeeded(
        self, cleanup_id: str, *, now: Any
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentSandboxCleanupORM)
                .where(
                    AgentSandboxCleanupORM.cleanup_id == cleanup_id,
                    AgentSandboxCleanupORM.status == SandboxCleanupStatus.PENDING.value,
                )
                .values(
                    status=SandboxCleanupStatus.SUCCEEDED.value,
                    completed_at=now,
                    updated_at=now,
                    last_error_code=None,
                    last_error_summary=None,
                )
                .returning(AgentSandboxCleanupORM.cleanup_id)
            )
            changed = result.scalar_one_or_none() is not None
            if changed:
                await session.commit()
            return changed

    async def enqueue_due_cleanups(self, *, now: Any, limit: int) -> int:
        """用 generation/fence/状态条件退役 Lease，并原子建立销毁事实。"""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(AgentSandboxLeaseORM, AgentSessionORM.status)
                    .join(
                        AgentSessionORM,
                        AgentSessionORM.session_id == AgentSandboxLeaseORM.session_id,
                    )
                    .where(
                        AgentSandboxLeaseORM.status
                        != SandboxLeaseStatus.RETIRED.value,
                        or_(
                            AgentSandboxLeaseORM.status
                            == SandboxLeaseStatus.DIRTY.value,
                            AgentSandboxLeaseORM.expires_at <= now,
                            AgentSessionORM.status == "closed",
                        ),
                    )
                    .order_by(AgentSandboxLeaseORM.updated_at, AgentSandboxLeaseORM.session_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True, of=AgentSandboxLeaseORM)
                )
            ).all()
            scheduled = 0
            for lease, session_status in rows:
                if session_status == "closed":
                    reason = "session_closed"
                    due_guard = AgentSandboxLeaseORM.session_id.in_(
                        select(AgentSessionORM.session_id).where(
                            AgentSessionORM.status == "closed"
                        )
                    )
                elif lease.status == SandboxLeaseStatus.DIRTY.value:
                    reason = "dirty"
                    due_guard = (
                        AgentSandboxLeaseORM.status == SandboxLeaseStatus.DIRTY.value
                    )
                else:
                    reason = "expired"
                    due_guard = and_(
                        AgentSandboxLeaseORM.status == SandboxLeaseStatus.ACTIVE.value,
                        AgentSandboxLeaseORM.expires_at <= now,
                    )
                retired = (
                    await session.execute(
                        update(AgentSandboxLeaseORM)
                        .where(
                            AgentSandboxLeaseORM.session_id == lease.session_id,
                            AgentSandboxLeaseORM.generation == lease.generation,
                            AgentSandboxLeaseORM.fencing_token == lease.fencing_token,
                            due_guard,
                        )
                        .values(
                            status=SandboxLeaseStatus.RETIRED.value,
                            updated_at=now,
                        )
                        .returning(AgentSandboxLeaseORM.session_id)
                    )
                ).scalar_one_or_none()
                if retired is None:
                    continue
                cleanup = create_sandbox_cleanup_task(
                    owner_id=lease.owner_id,
                    project_id=lease.project_id,
                    session_id=lease.session_id,
                    sandbox_id=lease.sandbox_id,
                    generation=lease.generation,
                    fencing_token=lease.fencing_token,
                    reason=reason,
                    now=now,
                )
                await session.execute(
                    insert(AgentSandboxCleanupORM)
                    .values(**_cleanup_values(cleanup))
                    .on_conflict_do_nothing(
                        index_elements=[AgentSandboxCleanupORM.cleanup_id]
                    )
                )
                scheduled += 1
            if scheduled:
                await session.commit()
            return scheduled

    async def claim_cleanup_tasks(
        self,
        *,
        worker_id: str,
        now: Any,
        lease_seconds: int,
        limit: int,
    ) -> tuple[SandboxCleanupTask, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(AgentSandboxCleanupORM)
                    .where(
                        or_(
                            and_(
                                AgentSandboxCleanupORM.status
                                == SandboxCleanupStatus.PENDING.value,
                                AgentSandboxCleanupORM.next_attempt_at <= now,
                            ),
                            and_(
                                AgentSandboxCleanupORM.status
                                == SandboxCleanupStatus.RUNNING.value,
                                AgentSandboxCleanupORM.lease_expires_at <= now,
                            ),
                        )
                    )
                    .order_by(
                        AgentSandboxCleanupORM.next_attempt_at,
                        AgentSandboxCleanupORM.cleanup_id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            claimed: list[SandboxCleanupTask] = []
            for row in rows:
                row.status = SandboxCleanupStatus.RUNNING.value
                row.attempt_count += 1
                row.lease_owner_id = worker_id
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                row.updated_at = now
                claimed.append(_cleanup(row))
            if claimed:
                await session.commit()
            return tuple(claimed)

    async def complete_cleanup(
        self,
        cleanup_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        now: Any,
    ) -> bool:
        return await self._finish_cleanup_attempt(
            cleanup_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            values={
                "status": SandboxCleanupStatus.SUCCEEDED.value,
                "lease_owner_id": None,
                "lease_expires_at": None,
                "last_error_code": None,
                "last_error_summary": None,
                "completed_at": now,
                "updated_at": now,
            },
        )

    async def retry_cleanup(
        self,
        cleanup_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        next_attempt_at: Any,
        error_code: str,
        error_summary: str,
        now: Any,
    ) -> bool:
        return await self._finish_cleanup_attempt(
            cleanup_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            values={
                "status": SandboxCleanupStatus.PENDING.value,
                "next_attempt_at": next_attempt_at,
                "lease_owner_id": None,
                "lease_expires_at": None,
                "last_error_code": error_code,
                "last_error_summary": error_summary,
                "completed_at": None,
                "updated_at": now,
            },
        )

    async def _finish_cleanup_attempt(
        self,
        cleanup_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        values: dict[str, Any],
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentSandboxCleanupORM)
                .where(
                    AgentSandboxCleanupORM.cleanup_id == cleanup_id,
                    AgentSandboxCleanupORM.status == SandboxCleanupStatus.RUNNING.value,
                    AgentSandboxCleanupORM.lease_owner_id == worker_id,
                    AgentSandboxCleanupORM.attempt_count == attempt_count,
                )
                .values(**values)
                .returning(AgentSandboxCleanupORM.cleanup_id)
            )
            changed = result.scalar_one_or_none() is not None
            if changed:
                await session.commit()
            return changed

    async def mark_dirty(
        self, session_id: str, generation: int, fencing_token: int
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentSandboxLeaseORM)
                .where(
                    AgentSandboxLeaseORM.session_id == session_id,
                    AgentSandboxLeaseORM.generation == generation,
                    AgentSandboxLeaseORM.fencing_token == fencing_token,
                    AgentSandboxLeaseORM.status == SandboxLeaseStatus.ACTIVE.value,
                )
                .values(status=SandboxLeaseStatus.DIRTY.value)
                .returning(AgentSandboxLeaseORM.session_id)
            )
            if result.scalar_one_or_none() is not None:
                await session.commit()
                return True
            return False

    async def latest_snapshot(self, session_id: str) -> WorkspaceSnapshot | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentWorkspaceSnapshotORM)
                    .where(
                        AgentWorkspaceSnapshotORM.session_id == session_id,
                        AgentWorkspaceSnapshotORM.status
                        == WorkspaceSnapshotStatus.STABLE.value,
                    )
                    .order_by(AgentWorkspaceSnapshotORM.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return _snapshot(row) if row is not None else None

    async def snapshot_for_turn(self, turn_run_id: str) -> WorkspaceSnapshot | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentWorkspaceSnapshotORM).where(
                        AgentWorkspaceSnapshotORM.turn_run_id == turn_run_id
                    )
                )
            ).scalar_one_or_none()
            return _snapshot(row) if row is not None else None

    async def stage_snapshot(
        self,
        value: WorkspaceSnapshot,
        *,
        lease_generation: int,
        fencing_token: int,
    ) -> bool:
        async with self._session_factory() as session:
            lease = (
                await session.execute(
                    select(AgentSandboxLeaseORM)
                    .where(AgentSandboxLeaseORM.session_id == value.session_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                lease is None
                or lease.status != SandboxLeaseStatus.ACTIVE.value
                or lease.generation != lease_generation
                or lease.fencing_token != fencing_token
                or lease.holder_turn_run_id != value.turn_run_id
            ):
                return False
            latest_version = (
                await session.execute(
                    select(AgentWorkspaceSnapshotORM.version)
                    .where(
                        AgentWorkspaceSnapshotORM.session_id == value.session_id,
                        AgentWorkspaceSnapshotORM.status
                        == WorkspaceSnapshotStatus.STABLE.value,
                    )
                    .order_by(AgentWorkspaceSnapshotORM.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if value.version != (1 if latest_version is None else latest_version + 1):
                return False
            result = await session.execute(
                insert(AgentWorkspaceSnapshotORM)
                .values(
                    snapshot_id=value.snapshot_id,
                    schema_version=value.schema_version,
                    owner_id=value.owner_id,
                    project_id=value.project_id,
                    session_id=value.session_id,
                    turn_run_id=value.turn_run_id,
                    version=value.version,
                    sandbox_generation=value.sandbox_generation,
                    files=[
                        {
                            "path": item.path,
                            "content_hash": item.content_hash,
                            "size_bytes": item.size_bytes,
                        }
                        for item in value.files
                    ],
                    total_size_bytes=value.total_size_bytes,
                    manifest_hash=value.manifest_hash,
                    status=WorkspaceSnapshotStatus.STAGED.value,
                    created_at=value.created_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[AgentWorkspaceSnapshotORM.turn_run_id]
                )
                .returning(AgentWorkspaceSnapshotORM.snapshot_id)
            )
            inserted = result.scalar_one_or_none() is not None
            if inserted:
                await session.commit()
            return inserted

class SqlalchemyWorkspaceSnapshotPublisher:
    """复用 AgentTurn 结果事务发布 STABLE，不执行任何外部 I/O。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def publish_for_success(
        self,
        *,
        owner_id: str,
        project_id: str,
        session_id: str,
        turn_run_id: str,
        required: bool,
    ) -> bool:
        row = (
            await self._session.execute(
                select(AgentWorkspaceSnapshotORM)
                .where(AgentWorkspaceSnapshotORM.turn_run_id == turn_run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return not required
        if (
            row.owner_id != owner_id
            or row.project_id != project_id
            or row.session_id != session_id
        ):
            return False
        execution = (
            await self._session.execute(
                select(AgentRuntimeExecutionORM)
                .where(AgentRuntimeExecutionORM.turn_run_id == turn_run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        run = await self._session.get(RunORM, turn_run_id)
        if (
            execution is None
            or execution.state != RuntimeControlState.SUCCEEDED.value
            or run is None
            or run.status != RunStatus.SUCCEEDED.value
        ):
            return False
        if row.status == WorkspaceSnapshotStatus.STABLE.value:
            return True
        latest_version = (
            await self._session.execute(
                select(AgentWorkspaceSnapshotORM.version)
                .where(
                    AgentWorkspaceSnapshotORM.session_id == session_id,
                    AgentWorkspaceSnapshotORM.status
                    == WorkspaceSnapshotStatus.STABLE.value,
                )
                .order_by(AgentWorkspaceSnapshotORM.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row.version != (1 if latest_version is None else latest_version + 1):
            return False
        row.status = WorkspaceSnapshotStatus.STABLE.value
        return True


def _lease_values(value: SandboxLeaseRecord) -> dict[str, Any]:
    return {
        "session_id": value.session_id,
        "owner_id": value.owner_id,
        "project_id": value.project_id,
        "holder_turn_run_id": value.holder_turn_run_id,
        "sandbox_id": value.sandbox_id,
        "image_ref": value.image_ref,
        "network_profile_id": value.network_profile_id,
        "network_profile_version": value.network_profile_version,
        "network_profile_hash": value.network_profile_hash,
        "generation": value.generation,
        "fencing_token": value.fencing_token,
        "status": value.status.value,
        "generation_started_at": value.generation_started_at,
        "expires_at": value.expires_at,
        "updated_at": value.updated_at,
    }


def _lease(row: AgentSandboxLeaseORM) -> SandboxLeaseRecord:
    return SandboxLeaseRecord(
        session_id=row.session_id,
        owner_id=row.owner_id,
        project_id=row.project_id,
        holder_turn_run_id=row.holder_turn_run_id,
        sandbox_id=row.sandbox_id,
        image_ref=row.image_ref,
        generation=row.generation,
        fencing_token=row.fencing_token,
        status=SandboxLeaseStatus(row.status),
        generation_started_at=row.generation_started_at,
        expires_at=row.expires_at,
        updated_at=row.updated_at,
        network_profile_id=row.network_profile_id,
        network_profile_version=row.network_profile_version,
        network_profile_hash=row.network_profile_hash,
    )


def _cleanup_values(value: SandboxCleanupTask) -> dict[str, Any]:
    return {
        "cleanup_id": value.cleanup_id,
        "owner_id": value.owner_id,
        "project_id": value.project_id,
        "session_id": value.session_id,
        "sandbox_id": value.sandbox_id,
        "generation": value.generation,
        "fencing_token": value.fencing_token,
        "reason": value.reason,
        "status": value.status.value,
        "attempt_count": value.attempt_count,
        "next_attempt_at": value.next_attempt_at,
        "lease_owner_id": value.lease_owner_id,
        "lease_expires_at": value.lease_expires_at,
        "last_error_code": value.last_error_code,
        "last_error_summary": value.last_error_summary,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "completed_at": value.completed_at,
    }


def _cleanup(row: AgentSandboxCleanupORM) -> SandboxCleanupTask:
    return SandboxCleanupTask(
        cleanup_id=row.cleanup_id,
        owner_id=row.owner_id,
        project_id=row.project_id,
        session_id=row.session_id,
        sandbox_id=row.sandbox_id,
        generation=row.generation,
        fencing_token=row.fencing_token,
        reason=row.reason,
        status=SandboxCleanupStatus(row.status),
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        lease_owner_id=row.lease_owner_id,
        lease_expires_at=row.lease_expires_at,
        last_error_code=row.last_error_code,
        last_error_summary=row.last_error_summary,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _snapshot(row: AgentWorkspaceSnapshotORM) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        snapshot_id=row.snapshot_id,
        schema_version=row.schema_version,
        owner_id=row.owner_id,
        project_id=row.project_id,
        session_id=row.session_id,
        turn_run_id=row.turn_run_id,
        version=row.version,
        sandbox_generation=row.sandbox_generation,
        files=tuple(WorkspaceFile(**item) for item in row.files),
        total_size_bytes=row.total_size_bytes,
        manifest_hash=row.manifest_hash,
        created_at=row.created_at,
        status=WorkspaceSnapshotStatus(row.status),
    )
