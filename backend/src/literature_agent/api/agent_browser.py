"""Research Agent 浏览器人工控制与受鉴权画面代理。"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from pydantic import BaseModel

from literature_agent.api.dependencies import ActorDep, CorrelationIdDep
from literature_agent.application.browser_control_service import (
    BrowserControlService,
    BrowserControlView,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    AgentSessionBusyError,
    AgentSessionNotFoundError,
    BrowserControlConflictError,
    BrowserControlNotFoundError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from literature_agent.infrastructure.agent.browser_gateway import (
    OpenSandboxBrowserTargetResolver,
    bridge_vnc_websocket,
    connect_browser_upstream,
    remaining_control_seconds,
)
from literature_agent.infrastructure.agent.browser_ticket import HmacBrowserTicketIssuer
from literature_agent.infrastructure.agent.opensandbox_backend import OpenSandboxProvider
from literature_agent.infrastructure.persistence.agent_repository import SqlalchemyAgentRepository
from literature_agent.infrastructure.persistence.browser_control_repository import (
    SqlalchemyBrowserControlRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository

router = APIRouter(prefix="/api/v1", tags=["agent-browser"])


class BrowserControlResponse(BaseModel):
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


class BrowserControlStatusResponse(BaseModel):
    control: BrowserControlResponse | None


class BrowserControlStartResponse(BaseModel):
    control: BrowserControlResponse
    ticket: str
    view_url: str


def _service(request: Request) -> BrowserControlService:
    state = request.app.state.app_state
    return BrowserControlService(
        session_factory=state.session_factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        browser_repo_factory=SqlalchemyBrowserControlRepository,
        project_repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
        ticket_issuer=HmacBrowserTicketIssuer(state.browser_ticket_secret),
        event_notifier=state.event_notifier,
    )


ServiceDep = Annotated[BrowserControlService, Depends(_service)]


def _response(value: BrowserControlView) -> BrowserControlResponse:
    return BrowserControlResponse(
        control_id=value.control_id,
        session_id=value.session_id,
        mode=value.mode,
        status=value.status,
        revision=value.revision,
        sandbox_generation=value.sandbox_generation,
        started_at=value.started_at,
        expires_at=value.expires_at,
        ended_at=value.ended_at,
        end_reason=value.end_reason,
        viewer_connected=value.viewer_connected,
    )


def _translate(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (AgentSessionNotFoundError, ProjectNotFoundError, BrowserControlNotFoundError),
    ):
        return HTTPException(status.HTTP_404_NOT_FOUND, "browser_control_not_found")
    if isinstance(exc, AgentSessionBusyError):
        return HTTPException(status.HTTP_409_CONFLICT, "agent_session_busy")
    if isinstance(exc, BrowserControlConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, "browser_control_unavailable")
    if isinstance(exc, ProjectArchivedError):
        return HTTPException(status.HTTP_409_CONFLICT, "project_archived")
    raise exc


def _browser_ticket_from_subprotocols(header_value: str) -> str | None:
    """只接受一个 bearer ticket 和固定 binary 协议，不从 URL 读取票据。"""
    offered = [item.strip() for item in header_value.split(",") if item.strip()]
    ticket_protocols = [
        item for item in offered if item.startswith("browser-ticket.")
    ]
    if "binary" not in offered or len(ticket_protocols) != 1:
        return None
    ticket = ticket_protocols[0].removeprefix("browser-ticket.")
    return ticket or None


@router.post(
    "/agent-sessions/{session_id}/browser-control",
    response_model=BrowserControlStartResponse,
)
async def start_browser_control(
    session_id: str,
    actor: ActorDep,
    correlation_id: CorrelationIdDep,
    service: ServiceDep,
) -> BrowserControlStartResponse:
    try:
        result = await service.start(actor, session_id, correlation_id=correlation_id)
    except Exception as exc:
        raise _translate(exc) from exc
    return BrowserControlStartResponse(
        control=_response(result.control),
        ticket=result.ticket,
        view_url=result.view_url,
    )


@router.get(
    "/agent-sessions/{session_id}/browser-control",
    response_model=BrowserControlStatusResponse,
)
async def get_browser_control(
    session_id: str,
    actor: ActorDep,
    correlation_id: CorrelationIdDep,
    service: ServiceDep,
) -> BrowserControlStatusResponse:
    try:
        value = await service.get(actor, session_id, correlation_id=correlation_id)
    except Exception as exc:
        raise _translate(exc) from exc
    return BrowserControlStatusResponse(control=_response(value) if value else None)


@router.delete(
    "/agent-sessions/{session_id}/browser-control",
    response_model=BrowserControlResponse,
)
async def end_browser_control(
    session_id: str,
    actor: ActorDep,
    correlation_id: CorrelationIdDep,
    service: ServiceDep,
) -> BrowserControlResponse:
    try:
        return _response(
            await service.end(actor, session_id, correlation_id=correlation_id)
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.websocket("/agent-browser-controls/view")
async def browser_control_view(websocket: WebSocket) -> None:
    """ticket 只经 WebSocket subprotocol 传递，不进入 URL 或持久日志。"""
    ticket = _browser_ticket_from_subprotocols(
        websocket.headers.get("sec-websocket-protocol", "")
    )
    if ticket is None:
        await websocket.close(code=4403)
        return
    state = websocket.app.state.app_state
    service = BrowserControlService(
        session_factory=state.session_factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        browser_repo_factory=SqlalchemyBrowserControlRepository,
        project_repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
        ticket_issuer=HmacBrowserTicketIssuer(state.browser_ticket_secret),
        event_notifier=state.event_notifier,
    )
    actor = ActorContext(owner_id=state.settings.dev_actor_id)
    connection_id = str(uuid4())
    try:
        grant = await service.claim_view(
            actor, ticket, connection_id=connection_id
        )
    except (BrowserControlNotFoundError, BrowserControlConflictError):
        await websocket.close(code=4403)
        return

    provider = OpenSandboxProvider(
        domain=state.settings.research_sandbox_domain,
        protocol=state.settings.research_sandbox_protocol,
        api_key=state.settings.research_sandbox_api_key,
    )
    resolver = OpenSandboxBrowserTargetResolver(state.session_factory, provider)
    upstream = None
    try:
        target = await resolver.resolve(grant)
        upstream = await connect_browser_upstream(target)
        await websocket.accept(subprotocol="binary")
        await bridge_vnc_websocket(
            websocket,
            upstream,
            is_current=lambda: service.view_is_current(grant),
            total_timeout_seconds=remaining_control_seconds(grant),
        )
        upstream = None
    except Exception:
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1011)
    finally:
        if upstream is not None:
            with suppress(Exception):
                await upstream.close()
        await service.release_view(grant)


__all__ = ["router"]
