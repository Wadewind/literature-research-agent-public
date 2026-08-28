from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.api.agent_browser import (
    _browser_ticket_from_subprotocols,
    end_browser_control,
    get_browser_control,
    router,
    start_browser_control,
)
from literature_agent.application.browser_control_service import (
    BrowserControlView,
    StartBrowserControlResult,
)
from literature_agent.domain.actor import ActorContext

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


class _Service:
    async def start(self, actor, session_id, *, correlation_id):
        assert actor.owner_id == "owner-1"
        return StartBrowserControlResult(
            _view(session_id),
            "opaque-ticket-value",
            "/api/v1/agent-browser-controls/view",
        )

    async def get(self, actor, session_id, *, correlation_id):
        return _view(session_id)

    async def end(self, actor, session_id, *, correlation_id):
        return replace(
            _view(session_id),
            status="ended",
            ended_at=NOW + timedelta(seconds=1),
            end_reason="user_completed",
        )


def test_browser_control_routes_are_mounted_on_application() -> None:
    from literature_agent.main import create_app

    app = create_app()
    assert any(getattr(value, "original_router", None) is router for value in app.routes)
    paths = {value.path for value in router.routes}
    assert "/api/v1/agent-sessions/{session_id}/browser-control" in paths
    assert "/api/v1/agent-browser-controls/view" in paths


@pytest.mark.parametrize(
    "header",
    [
        "binary",
        "browser-ticket.ticket",
        "binary, browser-ticket.",
        "binary, browser-ticket.first, browser-ticket.second",
    ],
)
def test_websocket_requires_one_ticket_subprotocol_and_binary(header: str) -> None:
    assert _browser_ticket_from_subprotocols(header) is None


def test_websocket_ticket_is_read_from_subprotocol_not_url() -> None:
    assert (
        _browser_ticket_from_subprotocols("binary, browser-ticket.opaque")
        == "opaque"
    )


def _view(session_id: str) -> BrowserControlView:
    return BrowserControlView(
        control_id="control-1",
        session_id=session_id,
        mode="manual",
        status="active",
        revision=1,
        sandbox_generation=3,
        started_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        ended_at=None,
        end_reason=None,
        viewer_connected=False,
    )


@pytest.mark.asyncio
async def test_browser_control_http_contract_hides_provider_endpoints() -> None:
    response = await start_browser_control(
        "session-1", ActorContext(owner_id="owner-1"), "correlation-1", _Service()
    )
    body = response.model_dump(mode="json")
    assert body["ticket"] == "opaque-ticket-value"
    assert body["view_url"] == "/api/v1/agent-browser-controls/view"
    serialized = response.model_dump_json().lower()
    for forbidden in ("sandbox_id", "vnc", "cdp", "mcp", "opensandbox", "endpoint"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_browser_control_query_and_end_use_business_session_id() -> None:
    actor = ActorContext(owner_id="owner-1")
    queried = await get_browser_control(
        "session-1", actor, "correlation-1", _Service()
    )
    ended = await end_browser_control(
        "session-1", actor, "correlation-2", _Service()
    )
    assert queried.control is not None
    assert queried.control.status == "active"
    assert ended.status == "ended"
