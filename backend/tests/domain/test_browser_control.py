from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.domain.browser_control import (
    BROWSER_CONTROL_MAX_TTL_SECONDS,
    BrowserControlStatus,
    browser_ticket_digest,
    create_browser_control_lease,
)

NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)


def _lease(**changes):
    values = {
        "owner_id": "owner-1",
        "project_id": "project-1",
        "session_id": "session-1",
        "anchor_turn_run_id": "turn-1",
        "sandbox_generation": 2,
        "sandbox_fencing_token": 3,
        "revision": 1,
        "ticket_digest": browser_ticket_digest("opaque-ticket"),
        "physical_expires_at": NOW + timedelta(minutes=10),
        "now": NOW,
        "control_id": "control-1",
    }
    values.update(changes)
    return create_browser_control_lease(**values)


def test_control_ttl_is_capped_by_platform_and_physical_lease() -> None:
    capped = _lease(ttl_seconds=999)
    physical = _lease(
        control_id="control-2",
        physical_expires_at=NOW + timedelta(seconds=30),
    )
    assert capped.expires_at == NOW + timedelta(seconds=BROWSER_CONTROL_MAX_TTL_SECONDS)
    assert physical.expires_at == NOW + timedelta(seconds=30)


def test_active_even_if_wall_clock_expired_blocks_turn_until_reconciled() -> None:
    value = _lease(physical_expires_at=NOW + timedelta(seconds=1))
    assert value.blocks_turn
    assert not value.is_live(NOW + timedelta(seconds=2))
    expired = value.expire(now=NOW + timedelta(seconds=2))
    assert expired.status is BrowserControlStatus.EXPIRED
    assert not expired.blocks_turn
    assert expired.end_reason == "ttl_expired"


def test_end_and_expire_are_idempotent_and_terminal_fields_are_consistent() -> None:
    value = _lease()
    ended = value.end(now=NOW + timedelta(seconds=1), reason="user_completed")
    assert ended.end(now=NOW + timedelta(seconds=2), reason="again") == ended
    assert ended.expire(now=NOW + timedelta(minutes=6)) == ended
    with pytest.raises(ValueError, match="结束事实"):
        replace(value, ended_at=NOW, end_reason="invalid")


@pytest.mark.parametrize(
    ("field", "value"),
    [("sandbox_generation", 0), ("sandbox_fencing_token", 0), ("revision", 0)],
)
def test_control_rejects_non_positive_fence_or_revision(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        _lease(**{field: value})


def test_control_rejects_expired_physical_lease_and_early_expire() -> None:
    with pytest.raises(ValueError, match="已到期"):
        _lease(physical_expires_at=NOW)
    with pytest.raises(ValueError, match="未到期"):
        _lease().expire(now=NOW)
