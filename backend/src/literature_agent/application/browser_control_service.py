"""Turn 边界浏览器人工控制 Application Service。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.browser_control_repository import (
    BrowserControlRepository,
    BrowserSandboxLeaseView,
)
from literature_agent.application.ports.browser_ticket import BrowserTicketIssuer
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.browser_control import (
    BROWSER_CONTROL_MAX_TTL_SECONDS,
    BrowserControlLease,
    BrowserControlStatus,
    browser_ticket_digest,
    create_browser_control_lease,
)
from literature_agent.domain.exceptions import (
    AgentSessionBusyError,
    AgentSessionNotFoundError,
    BrowserControlConflictError,
    BrowserControlNotFoundError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from literature_agent.domain.run import RunStatus

TSession = TypeVar("TSession", bound=Session)


@dataclass(frozen=True, slots=True)
class BrowserControlView:
    control_id: str
    session_id: str
    mode: str
    status: str
    revision: int
    sandbox_generation: int
    started_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    viewer_connected: bool


@dataclass(frozen=True, slots=True)
class StartBrowserControlResult:
    control: BrowserControlView
    ticket: str
    view_url: str


@dataclass(frozen=True, slots=True)
class BrowserViewGrant:
    control_id: str
    owner_id: str
    project_id: str
    session_id: str
    sandbox_generation: int
    sandbox_fencing_token: int
    revision: int
    connection_id: str
    expires_at: datetime


class BrowserControlService[TSession: Session]:
    """把 owner/Session/Turn/物理 Lease 互斥固定在平台短事务中。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        browser_repo_factory: Callable[[TSession], BrowserControlRepository],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
        ticket_issuer: BrowserTicketIssuer,
        event_notifier: EventNotifier | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl_seconds: int = BROWSER_CONTROL_MAX_TTL_SECONDS,
        view_url: str = "/api/v1/agent-browser-controls/view",
    ) -> None:
        self._session_factory = session_factory
        self._agent_repo_factory = agent_repo_factory
        self._browser_repo_factory = browser_repo_factory
        self._project_repo_factory = project_repo_factory
        self._run_repo_factory = run_repo_factory
        self._ticket_issuer = ticket_issuer
        self._event_notifier = event_notifier or NoopEventNotifier()
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._view_url = view_url

    async def start(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        correlation_id: str,
    ) -> StartBrowserControlResult:
        notify_run_id: str | None = None
        async with self._session_factory() as session:
            agent_repo = self._agent_repo_factory(session)
            agent_session = await agent_repo.get_session_scoped_for_update(
                session_id, actor.owner_id
            )
            if agent_session is None:
                raise AgentSessionNotFoundError(session_id)
            project = await self._project_repo_factory(session).get_by_id(
                agent_session.project_id
            )
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(agent_session.project_id)
            if project.is_archived:
                raise ProjectArchivedError(project.project_id)
            if agent_session.active_turn_run_id is not None:
                active = await self._run_repo_factory(session).get_by_id(
                    agent_session.active_turn_run_id
                )
                if active is not None and active.status not in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    raise AgentSessionBusyError(session_id)
                await agent_repo.release_active_turn(
                    session_id, agent_session.active_turn_run_id
                )

            now = self._clock()
            repo = self._browser_repo_factory(session)
            sandbox = await repo.get_sandbox_for_update(session_id)
            if not self._sandbox_available(sandbox, actor.owner_id, project.project_id, now):
                raise BrowserControlConflictError("当前会话没有可接管的活动浏览器环境")
            assert sandbox is not None
            current = await repo.get_current_for_update(session_id)
            if current is not None and current.status is BrowserControlStatus.ACTIVE:
                if not self._control_matches_sandbox(current, sandbox):
                    invalid = current.invalidate(now=now, reason="sandbox_generation_changed")
                    await repo.save(invalid, expected_revision=current.revision)
                    await self._append_event(
                        repo, invalid, "agent_browser_control_expired", correlation_id
                    )
                    notify_run_id = invalid.anchor_turn_run_id
                    current = invalid
                elif not current.is_live(now):
                    expired = current.expire(now=now)
                    await repo.save(expired, expected_revision=current.revision)
                    await self._append_event(
                        repo, expired, "agent_browser_control_expired", correlation_id
                    )
                    notify_run_id = expired.anchor_turn_run_id
                    current = expired
                else:
                    ticket = self._ticket_issuer.issue(current.control_id, current.revision)
                    if browser_ticket_digest(ticket) == current.ticket_digest:
                        await session.commit()
                        return StartBrowserControlResult(
                            _view(current), ticket, self._view_url
                        )
                    invalid = current.invalidate(
                        now=now, reason="ticket_signing_key_changed"
                    )
                    await repo.save(invalid, expected_revision=current.revision)
                    await self._append_event(
                        repo, invalid, "agent_browser_control_expired", correlation_id
                    )
                    notify_run_id = invalid.anchor_turn_run_id
                    current = invalid

            revision = await repo.next_revision(session_id)
            control_id = str(uuid4())
            ticket = self._ticket_issuer.issue(control_id, revision)
            value = create_browser_control_lease(
                owner_id=actor.owner_id,
                project_id=project.project_id,
                session_id=session_id,
                anchor_turn_run_id=sandbox.holder_turn_run_id,
                sandbox_generation=sandbox.generation,
                sandbox_fencing_token=sandbox.fencing_token,
                revision=revision,
                ticket_digest=browser_ticket_digest(ticket),
                physical_expires_at=sandbox.expires_at,
                now=now,
                ttl_seconds=self._ttl_seconds,
                control_id=control_id,
            )
            await repo.add(value)
            await self._append_event(
                repo, value, "agent_browser_control_started", correlation_id
            )
            notify_run_id = value.anchor_turn_run_id
            await session.commit()
        if notify_run_id:
            await notify_run_event(self._event_notifier, notify_run_id)
        return StartBrowserControlResult(_view(value), ticket, self._view_url)

    async def get(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        correlation_id: str,
    ) -> BrowserControlView | None:
        notify_run_id: str | None = None
        async with self._session_factory() as session:
            agent_session = await self._agent_repo_factory(session).get_session_scoped(
                session_id, actor.owner_id
            )
            if agent_session is None:
                raise AgentSessionNotFoundError(session_id)
            repo = self._browser_repo_factory(session)
            current = await repo.get_current_for_update(session_id)
            if current is None:
                return None
            if current.owner_id != actor.owner_id:
                raise AgentSessionNotFoundError(session_id)
            now = self._clock()
            if current.status is BrowserControlStatus.ACTIVE:
                sandbox = await repo.get_sandbox_for_update(session_id)
                if sandbox is None or not self._control_matches_sandbox(current, sandbox):
                    current = current.invalidate(
                        now=now, reason="sandbox_generation_changed"
                    )
                elif not current.is_live(now):
                    current = current.expire(now=now)
                else:
                    return _view(current)
                await repo.save(current, expected_revision=current.revision)
                await self._append_event(
                    repo, current, "agent_browser_control_expired", correlation_id
                )
                notify_run_id = current.anchor_turn_run_id
                await session.commit()
        if notify_run_id:
            await notify_run_event(self._event_notifier, notify_run_id)
        return _view(current)

    async def end(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        correlation_id: str,
    ) -> BrowserControlView:
        notify_run_id: str | None = None
        async with self._session_factory() as session:
            if (
                await self._agent_repo_factory(session).get_session_scoped_for_update(
                    session_id, actor.owner_id
                )
                is None
            ):
                raise AgentSessionNotFoundError(session_id)
            repo = self._browser_repo_factory(session)
            current = await repo.get_current_for_update(session_id)
            if current is None or current.owner_id != actor.owner_id:
                raise BrowserControlNotFoundError(session_id)
            if current.status is BrowserControlStatus.ACTIVE:
                now = self._clock()
                sandbox = await repo.get_sandbox_for_update(session_id)
                if sandbox is None or not self._control_matches_sandbox(current, sandbox):
                    ended = current.invalidate(
                        now=now, reason="sandbox_generation_changed"
                    )
                elif not current.is_live(now):
                    ended = current.expire(now=now)
                else:
                    ended = current.end(now=now, reason="user_completed")
                if not await repo.save(ended, expected_revision=current.revision):
                    raise BrowserControlConflictError("浏览器控制权已被并发修改")
                event_type = (
                    "agent_browser_control_expired"
                    if ended.status is BrowserControlStatus.EXPIRED
                    else "agent_browser_control_ended"
                )
                await self._append_event(repo, ended, event_type, correlation_id)
                notify_run_id = ended.anchor_turn_run_id
                current = ended
            await session.commit()
        if notify_run_id:
            await notify_run_event(self._event_notifier, notify_run_id)
        return _view(current)

    async def claim_view(
        self,
        actor: ActorContext,
        ticket: str,
        *,
        connection_id: str,
    ) -> BrowserViewGrant:
        if len(ticket) != 43 or any(
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for char in ticket
        ):
            raise BrowserControlNotFoundError("ticket")
        digest = browser_ticket_digest(ticket)
        async with self._session_factory() as session:
            repo = self._browser_repo_factory(session)
            current = await repo.get_by_ticket_digest_for_update(digest)
            now = self._clock()
            if (
                current is None
                or current.owner_id != actor.owner_id
                or not current.is_live(now)
            ):
                raise BrowserControlNotFoundError("ticket")
            sandbox = await repo.get_sandbox_for_update(current.session_id)
            if sandbox is None or not self._control_matches_sandbox(current, sandbox):
                raise BrowserControlNotFoundError("ticket")
            agent_session = await self._agent_repo_factory(session).get_session_scoped_for_update(
                current.session_id, actor.owner_id
            )
            if agent_session is None or agent_session.active_turn_run_id is not None:
                raise BrowserControlConflictError("Agent Turn 已开始，不能接管浏览器")
            if current.viewer_connection_id is not None:
                raise BrowserControlConflictError("浏览器已经由另一个视图控制")
            claimed = replace(current, viewer_connection_id=connection_id)
            if not await repo.save(claimed, expected_revision=current.revision):
                raise BrowserControlConflictError("浏览器视图认领冲突")
            await session.commit()
            return BrowserViewGrant(
                control_id=claimed.control_id,
                owner_id=claimed.owner_id,
                project_id=claimed.project_id,
                session_id=claimed.session_id,
                sandbox_generation=claimed.sandbox_generation,
                sandbox_fencing_token=claimed.sandbox_fencing_token,
                revision=claimed.revision,
                connection_id=connection_id,
                expires_at=claimed.expires_at,
            )

    async def release_view(self, grant: BrowserViewGrant) -> None:
        async with self._session_factory() as session:
            repo = self._browser_repo_factory(session)
            current = await repo.get_by_id_for_update(grant.control_id)
            if (
                current is not None
                and current.viewer_connection_id == grant.connection_id
            ):
                await repo.save(
                    replace(current, viewer_connection_id=None),
                    expected_revision=current.revision,
                )
                await session.commit()

    async def view_is_current(self, grant: BrowserViewGrant) -> bool:
        async with self._session_factory() as session:
            repo = self._browser_repo_factory(session)
            current = await repo.get_current(grant.session_id)
            if (
                current is None
                or current.control_id != grant.control_id
                or current.owner_id != grant.owner_id
                or current.revision != grant.revision
                or current.viewer_connection_id != grant.connection_id
                or not current.is_live(self._clock())
            ):
                return False
            sandbox = await repo.get_sandbox_for_update(grant.session_id)
            return sandbox is not None and self._control_matches_sandbox(current, sandbox)

    @staticmethod
    def _sandbox_available(
        sandbox: BrowserSandboxLeaseView | None,
        owner_id: str,
        project_id: str,
        now: datetime,
    ) -> bool:
        return bool(
            sandbox
            and sandbox.owner_id == owner_id
            and sandbox.project_id == project_id
            and sandbox.status == "active"
            and sandbox.expires_at > now
        )

    @staticmethod
    def _control_matches_sandbox(
        control: BrowserControlLease, sandbox: BrowserSandboxLeaseView
    ) -> bool:
        return (
            control.owner_id == sandbox.owner_id
            and control.project_id == sandbox.project_id
            and control.session_id == sandbox.session_id
            and control.sandbox_generation == sandbox.generation
            and control.sandbox_fencing_token == sandbox.fencing_token
            and sandbox.status == "active"
            and sandbox.expires_at >= control.expires_at
        )

    @staticmethod
    async def _append_event(
        repo: BrowserControlRepository,
        value: BrowserControlLease,
        event_type: str,
        correlation_id: str,
    ) -> None:
        payload: dict[str, object] = {
            "session_id": value.session_id,
            "sandbox_generation": value.sandbox_generation,
            "revision": value.revision,
        }
        if value.end_reason is not None:
            payload["reason"] = value.end_reason
        await repo.append_event(
            run_id=value.anchor_turn_run_id,
            event_type=event_type,
            correlation_id=correlation_id,
            payload=payload,
        )


def _view(value: BrowserControlLease) -> BrowserControlView:
    return BrowserControlView(
        control_id=value.control_id,
        session_id=value.session_id,
        mode=value.mode.value,
        status=value.status.value,
        revision=value.revision,
        sandbox_generation=value.sandbox_generation,
        started_at=value.started_at,
        expires_at=value.expires_at,
        ended_at=value.ended_at,
        end_reason=value.end_reason,
        viewer_connected=value.viewer_connection_id is not None,
    )
