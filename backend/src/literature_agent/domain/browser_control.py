"""Research Agent 浏览器人工控制权领域模型。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

BROWSER_CONTROL_MAX_TTL_SECONDS = 300
BROWSER_TICKET_DIGEST_LENGTH = 64


class BrowserControlMode(StrEnum):
    """只持久化人工控制；不存在 ACTIVE Lease 即表示 Agent/idle。"""

    MANUAL = "manual"


class BrowserControlStatus(StrEnum):
    """业务控制权状态。"""

    ACTIVE = "active"
    ENDED = "ended"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class BrowserControlLease:
    """绑定 Session/generation 的短时 MANUAL 事实；Agent/idle 不创建 Lease。"""

    control_id: str
    owner_id: str
    project_id: str
    session_id: str
    anchor_turn_run_id: str
    sandbox_generation: int
    sandbox_fencing_token: int
    mode: BrowserControlMode
    status: BrowserControlStatus
    revision: int
    ticket_digest: str
    started_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    viewer_connection_id: str | None = None

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.control_id,
                self.owner_id,
                self.project_id,
                self.session_id,
                self.anchor_turn_run_id,
            )
        ):
            raise ValueError("BrowserControlLease scope 不能为空")
        if self.sandbox_generation < 1 or self.sandbox_fencing_token < 1:
            raise ValueError("BrowserControlLease generation/fence 必须为正数")
        if self.revision < 1:
            raise ValueError("BrowserControlLease revision 必须为正数")
        if (
            len(self.ticket_digest) != BROWSER_TICKET_DIGEST_LENGTH
            or any(char not in "0123456789abcdef" for char in self.ticket_digest)
        ):
            raise ValueError("BrowserControlLease ticket digest 非法")
        if self.started_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("BrowserControlLease 时间必须带时区")
        ttl = self.expires_at - self.started_at
        if ttl <= timedelta(0) or ttl > timedelta(seconds=BROWSER_CONTROL_MAX_TTL_SECONDS):
            raise ValueError("BrowserControlLease TTL 必须在平台上限内")
        if self.status is BrowserControlStatus.ACTIVE:
            if self.ended_at is not None or self.end_reason is not None:
                raise ValueError("活动 BrowserControlLease 不能已有结束事实")
        elif self.ended_at is None or not (self.end_reason or "").strip():
            raise ValueError("终态 BrowserControlLease 必须记录结束时间和原因")

    @property
    def blocks_turn(self) -> bool:
        """未 reconcile 的过期 ACTIVE 也必须阻止新 Turn。"""
        return self.status is BrowserControlStatus.ACTIVE

    def is_live(self, now: datetime) -> bool:
        return self.status is BrowserControlStatus.ACTIVE and now < self.expires_at

    def end(self, *, now: datetime, reason: str) -> BrowserControlLease:
        """幂等结束；保留原始终态。"""
        if self.status is not BrowserControlStatus.ACTIVE:
            return self
        if not reason.strip():
            raise ValueError("BrowserControlLease 结束原因不能为空")
        return replace(
            self,
            status=BrowserControlStatus.ENDED,
            ended_at=now,
            end_reason=reason,
            viewer_connection_id=None,
        )

    def expire(self, *, now: datetime) -> BrowserControlLease:
        """只在到期后幂等收敛为 EXPIRED。"""
        if self.status is not BrowserControlStatus.ACTIVE:
            return self
        if now < self.expires_at:
            raise ValueError("未到期 BrowserControlLease 不能标记过期")
        return replace(
            self,
            status=BrowserControlStatus.EXPIRED,
            ended_at=now,
            end_reason="ttl_expired",
            viewer_connection_id=None,
        )

    def invalidate(self, *, now: datetime, reason: str) -> BrowserControlLease:
        """generation/fence 变化时立即使旧控制权失效。"""
        if self.status is not BrowserControlStatus.ACTIVE:
            return self
        if not reason.strip():
            raise ValueError("BrowserControlLease 失效原因不能为空")
        return replace(
            self,
            status=BrowserControlStatus.EXPIRED,
            ended_at=now,
            end_reason=reason,
            viewer_connection_id=None,
        )


def browser_ticket_digest(ticket: str) -> str:
    """只持久化固定长度摘要，不保存短时票据正文。"""
    if not ticket:
        raise ValueError("Browser ticket 不能为空")
    return hashlib.sha256(ticket.encode("ascii")).hexdigest()


def create_browser_control_lease(
    *,
    owner_id: str,
    project_id: str,
    session_id: str,
    anchor_turn_run_id: str,
    sandbox_generation: int,
    sandbox_fencing_token: int,
    revision: int,
    ticket_digest: str,
    physical_expires_at: datetime,
    now: datetime | None = None,
    ttl_seconds: int = BROWSER_CONTROL_MAX_TTL_SECONDS,
    control_id: str | None = None,
) -> BrowserControlLease:
    """创建不超过物理 Sandbox Lease 和平台上限的人工控制权。"""
    current = now or datetime.now(UTC)
    if ttl_seconds <= 0:
        raise ValueError("BrowserControlLease TTL 必须为正数")
    expires_at = min(
        physical_expires_at,
        current + timedelta(seconds=min(ttl_seconds, BROWSER_CONTROL_MAX_TTL_SECONDS)),
    )
    if expires_at <= current:
        raise ValueError("Sandbox Lease 已到期")
    return BrowserControlLease(
        control_id=control_id or str(uuid4()),
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        anchor_turn_run_id=anchor_turn_run_id,
        sandbox_generation=sandbox_generation,
        sandbox_fencing_token=sandbox_fencing_token,
        mode=BrowserControlMode.MANUAL,
        status=BrowserControlStatus.ACTIVE,
        revision=revision,
        ticket_digest=ticket_digest,
        started_at=current,
        expires_at=expires_at,
        ended_at=None,
        end_reason=None,
    )
