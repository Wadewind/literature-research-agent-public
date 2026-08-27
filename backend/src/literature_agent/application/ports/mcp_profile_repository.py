"""Session MCP Profile 持久化端口。"""

from typing import Protocol

from literature_agent.domain.mcp_configuration import McpProfile


class McpProfileRepository(Protocol):
    async def get_scoped(self, session_id: str, owner_id: str) -> McpProfile | None: ...
    async def get_revision_scoped(
        self,
        profile_id: str,
        revision: int,
        session_id: str,
        owner_id: str,
    ) -> McpProfile | None: ...
    async def get_scoped_for_update(self, session_id: str, owner_id: str) -> McpProfile | None: ...
    async def add(self, value: McpProfile) -> McpProfile: ...
    async def save(self, value: McpProfile, *, expected_revision: int) -> bool: ...
