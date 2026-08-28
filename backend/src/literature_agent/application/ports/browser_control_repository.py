"""BrowserControlLease 持久化端口。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from literature_agent.domain.browser_control import BrowserControlLease


@dataclass(frozen=True, slots=True)
class BrowserSandboxLeaseView:
    """Application 可见的物理 Lease 安全投影，不包含 Provider 标识或 endpoint。"""

    owner_id: str
    project_id: str
    session_id: str
    holder_turn_run_id: str
    generation: int
    fencing_token: int
    status: str
    expires_at: datetime


class BrowserControlRepository(Protocol):
    async def get_current_for_update(
        self, session_id: str
    ) -> BrowserControlLease | None: ...
    async def get_current(self, session_id: str) -> BrowserControlLease | None: ...
    async def get_by_ticket_digest_for_update(
        self, ticket_digest: str
    ) -> BrowserControlLease | None: ...
    async def get_by_id_for_update(
        self, control_id: str
    ) -> BrowserControlLease | None: ...
    async def get_sandbox_for_update(
        self, session_id: str
    ) -> BrowserSandboxLeaseView | None: ...
    async def next_revision(self, session_id: str) -> int: ...
    async def add(self, value: BrowserControlLease) -> BrowserControlLease: ...
    async def save(
        self, value: BrowserControlLease, *, expected_revision: int
    ) -> bool: ...
    async def append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        correlation_id: str,
        payload: dict[str, object],
    ) -> None: ...
