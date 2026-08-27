"""MCP Catalog 查询与 owner/Session Profile 配置用例。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TypeVar

from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.mcp_profile_repository import McpProfileRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    AgentSessionNotFoundError,
    McpProfileInvalidError,
    McpProfileRevisionConflictError,
)
from literature_agent.domain.mcp_configuration import (
    McpCatalog,
    McpCatalogEntry,
    McpProfile,
    McpProfileSelection,
    canonical_json_hash,
    create_mcp_profile,
    update_mcp_profile,
)

TSession = TypeVar("TSession", bound=Session)


@dataclass(frozen=True, slots=True)
class McpProfileView:
    session_id: str
    revision: int
    selections: tuple[McpProfileSelection, ...]
    config_hash: str


class McpConfigurationService[TSession: Session]:
    """只接受 Catalog 选择，不接受 MCP 原始连接配置。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        profile_repo_factory: Callable[[TSession], McpProfileRepository],
        catalog: McpCatalog,
    ) -> None:
        self._session_factory = session_factory
        self._agent_repo_factory = agent_repo_factory
        self._profile_repo_factory = profile_repo_factory
        self._catalog = catalog

    def list_catalog(self) -> tuple[McpCatalogEntry, ...]:
        return self._catalog.entries

    async def get_profile(self, actor: ActorContext, session_id: str) -> McpProfileView:
        async with self._session_factory() as session:
            if (
                await self._agent_repo_factory(session).get_session_scoped(
                    session_id, actor.owner_id
                )
                is None
            ):
                raise AgentSessionNotFoundError(session_id)
            value = await self._profile_repo_factory(session).get_scoped(session_id, actor.owner_id)
            return _view(value, session_id)

    async def update_profile(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        expected_revision: int,
        selections: tuple[McpProfileSelection, ...],
    ) -> McpProfileView:
        if expected_revision < 0:
            raise McpProfileRevisionConflictError(session_id)
        try:
            for selection in selections:
                self._catalog.validate_selection(selection)
        except ValueError as exc:
            raise McpProfileInvalidError(str(exc)) from exc
        async with self._session_factory() as session:
            agent_session = await self._agent_repo_factory(session).get_session_scoped_for_update(
                session_id, actor.owner_id
            )
            if agent_session is None:
                raise AgentSessionNotFoundError(session_id)
            repo = self._profile_repo_factory(session)
            current = await repo.get_scoped_for_update(session_id, actor.owner_id)
            if current is None:
                if expected_revision != 0:
                    raise McpProfileRevisionConflictError(session_id)
                try:
                    value = create_mcp_profile(
                        owner_id=actor.owner_id,
                        session_id=session_id,
                        selections=selections,
                    )
                except ValueError as exc:
                    raise McpProfileInvalidError(str(exc)) from exc
                await repo.add(value)
            else:
                if current.revision != expected_revision:
                    raise McpProfileRevisionConflictError(session_id)
                try:
                    value = update_mcp_profile(current, selections=selections)
                except ValueError as exc:
                    raise McpProfileInvalidError(str(exc)) from exc
                if not await repo.save(value, expected_revision=expected_revision):
                    raise McpProfileRevisionConflictError(session_id)
            await session.commit()
            return _view(value, session_id)


def _view(value: McpProfile | None, session_id: str) -> McpProfileView:
    if value is None:
        return McpProfileView(
            session_id=session_id,
            revision=0,
            selections=(),
            config_hash=canonical_json_hash([]),
        )
    return McpProfileView(
        session_id=value.session_id,
        revision=value.revision,
        selections=value.selections,
        config_hash=value.config_hash,
    )
