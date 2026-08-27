"""Session MCP Profile 的 PostgreSQL Repository。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.mcp_profile_repository import McpProfileRepository
from literature_agent.domain.mcp_configuration import McpProfile, McpProfileSelection
from literature_agent.infrastructure.persistence.models import (
    AgentMcpProfileORM,
    AgentSessionORM,
)


class SqlalchemyMcpProfileRepository(McpProfileRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_scoped(self, session_id: str, owner_id: str) -> McpProfile | None:
        return await self._get_scoped(session_id, owner_id, for_update=False)

    async def get_scoped_for_update(self, session_id: str, owner_id: str) -> McpProfile | None:
        return await self._get_scoped(session_id, owner_id, for_update=True)

    async def get_revision_scoped(
        self,
        profile_id: str,
        revision: int,
        session_id: str,
        owner_id: str,
    ) -> McpProfile | None:
        statement = (
            select(AgentMcpProfileORM)
            .join(AgentSessionORM, AgentMcpProfileORM.session_id == AgentSessionORM.session_id)
            .where(
                AgentMcpProfileORM.profile_id == profile_id,
                AgentMcpProfileORM.revision == revision,
                AgentMcpProfileORM.session_id == session_id,
                AgentMcpProfileORM.owner_id == owner_id,
                AgentSessionORM.owner_id == owner_id,
            )
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def _get_scoped(
        self, session_id: str, owner_id: str, *, for_update: bool
    ) -> McpProfile | None:
        statement = (
            select(AgentMcpProfileORM)
            .join(AgentSessionORM, AgentMcpProfileORM.session_id == AgentSessionORM.session_id)
            .where(
                AgentMcpProfileORM.session_id == session_id,
                AgentMcpProfileORM.owner_id == owner_id,
                AgentSessionORM.owner_id == owner_id,
            )
            .order_by(AgentMcpProfileORM.revision.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def add(self, value: McpProfile) -> McpProfile:
        self._session.add(
            AgentMcpProfileORM(
                profile_id=value.profile_id,
                session_id=value.session_id,
                owner_id=value.owner_id,
                revision=value.revision,
                selections=_serialize_selections(value.selections),
                config_hash=value.config_hash,
                created_at=value.created_at,
                updated_at=value.updated_at,
            )
        )
        return value

    async def save(self, value: McpProfile, *, expected_revision: int) -> bool:
        current = await self.get_scoped_for_update(value.session_id, value.owner_id)
        if (
            current is None
            or current.profile_id != value.profile_id
            or current.revision != expected_revision
            or value.revision != expected_revision + 1
        ):
            return False
        await self.add(value)
        return True


def _serialize_selections(values: tuple[McpProfileSelection, ...]) -> list[dict]:
    return [
        {
            "catalog_id": item.catalog_id,
            "version": item.version,
            "parameters": dict(item.parameters),
        }
        for item in values
    ]


def _to_domain(row: AgentMcpProfileORM) -> McpProfile:
    return McpProfile(
        profile_id=row.profile_id,
        owner_id=row.owner_id,
        session_id=row.session_id,
        revision=row.revision,
        selections=tuple(
            McpProfileSelection(
                catalog_id=item["catalog_id"],
                version=item["version"],
                parameters=tuple(sorted(item.get("parameters", {}).items())),
            )
            for item in row.selections
        ),
        config_hash=row.config_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
