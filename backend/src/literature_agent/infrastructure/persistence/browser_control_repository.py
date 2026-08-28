"""BrowserControlLease PostgreSQL Adapter。"""

from __future__ import annotations

from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.browser_control_repository import (
    BrowserControlRepository,
    BrowserSandboxLeaseView,
)
from literature_agent.domain.browser_control import (
    BrowserControlLease,
    BrowserControlMode,
    BrowserControlStatus,
)
from literature_agent.domain.event import create_event
from literature_agent.infrastructure.persistence.event_repository import _to_orm
from literature_agent.infrastructure.persistence.models import (
    AgentBrowserControlLeaseORM,
    AgentSandboxLeaseORM,
    RunORM,
)


class SqlalchemyBrowserControlRepository(BrowserControlRepository):
    """所有方法复用 Application 提供的短事务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current_for_update(
        self, session_id: str
    ) -> BrowserControlLease | None:
        row = (
            await self._session.execute(
                select(AgentBrowserControlLeaseORM)
                .where(AgentBrowserControlLeaseORM.session_id == session_id)
                .order_by(AgentBrowserControlLeaseORM.revision.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        return _control(row) if row is not None else None

    async def get_current(self, session_id: str) -> BrowserControlLease | None:
        row = (
            await self._session.execute(
                select(AgentBrowserControlLeaseORM)
                .where(AgentBrowserControlLeaseORM.session_id == session_id)
                .order_by(AgentBrowserControlLeaseORM.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _control(row) if row is not None else None

    async def get_by_ticket_digest_for_update(
        self, ticket_digest: str
    ) -> BrowserControlLease | None:
        row = (
            await self._session.execute(
                select(AgentBrowserControlLeaseORM)
                .where(AgentBrowserControlLeaseORM.ticket_digest == ticket_digest)
                .with_for_update()
            )
        ).scalar_one_or_none()
        return _control(row) if row is not None else None

    async def get_by_id_for_update(
        self, control_id: str
    ) -> BrowserControlLease | None:
        row = (
            await self._session.execute(
                select(AgentBrowserControlLeaseORM)
                .where(AgentBrowserControlLeaseORM.control_id == control_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        return _control(row) if row is not None else None

    async def get_sandbox_for_update(
        self, session_id: str
    ) -> BrowserSandboxLeaseView | None:
        row = (
            await self._session.execute(
                select(AgentSandboxLeaseORM)
                .where(AgentSandboxLeaseORM.session_id == session_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return BrowserSandboxLeaseView(
            owner_id=row.owner_id,
            project_id=row.project_id,
            session_id=row.session_id,
            holder_turn_run_id=row.holder_turn_run_id,
            generation=row.generation,
            fencing_token=row.fencing_token,
            status=row.status,
            expires_at=row.expires_at,
        )

    async def next_revision(self, session_id: str) -> int:
        maximum = (
            await self._session.execute(
                select(func.max(AgentBrowserControlLeaseORM.revision)).where(
                    AgentBrowserControlLeaseORM.session_id == session_id
                )
            )
        ).scalar_one()
        return int(maximum or 0) + 1

    async def add(self, value: BrowserControlLease) -> BrowserControlLease:
        self._session.add(AgentBrowserControlLeaseORM(**_values(value)))
        return value

    async def save(
        self, value: BrowserControlLease, *, expected_revision: int
    ) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentBrowserControlLeaseORM)
                .where(
                    AgentBrowserControlLeaseORM.control_id == value.control_id,
                    AgentBrowserControlLeaseORM.revision == expected_revision,
                )
                .values(**_values(value))
            ),
        )
        return result.rowcount == 1

    async def append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        correlation_id: str,
        payload: dict[str, object],
    ) -> None:
        run = (
            await self._session.execute(
                select(RunORM).where(RunORM.run_id == run_id).with_for_update()
            )
        ).scalar_one()
        sequence = run.event_sequence
        self._session.add(
            _to_orm(
                create_event(
                    run_id,
                    sequence,
                    event_type,
                    "user",
                    correlation_id,
                    payload,
                )
            )
        )
        run.event_sequence = sequence + 1


def _values(value: BrowserControlLease) -> dict[str, object]:
    return {
        "control_id": value.control_id,
        "owner_id": value.owner_id,
        "project_id": value.project_id,
        "session_id": value.session_id,
        "anchor_turn_run_id": value.anchor_turn_run_id,
        "sandbox_generation": value.sandbox_generation,
        "sandbox_fencing_token": value.sandbox_fencing_token,
        "mode": value.mode.value,
        "status": value.status.value,
        "revision": value.revision,
        "ticket_digest": value.ticket_digest,
        "viewer_connection_id": value.viewer_connection_id,
        "started_at": value.started_at,
        "expires_at": value.expires_at,
        "ended_at": value.ended_at,
        "end_reason": value.end_reason,
    }


def _control(row: AgentBrowserControlLeaseORM) -> BrowserControlLease:
    return BrowserControlLease(
        control_id=row.control_id,
        owner_id=row.owner_id,
        project_id=row.project_id,
        session_id=row.session_id,
        anchor_turn_run_id=row.anchor_turn_run_id,
        sandbox_generation=row.sandbox_generation,
        sandbox_fencing_token=row.sandbox_fencing_token,
        mode=BrowserControlMode(row.mode),
        status=BrowserControlStatus(row.status),
        revision=row.revision,
        ticket_digest=row.ticket_digest,
        viewer_connection_id=row.viewer_connection_id,
        started_at=row.started_at,
        expires_at=row.expires_at,
        ended_at=row.ended_at,
        end_reason=row.end_reason,
    )
