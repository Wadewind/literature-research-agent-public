"""把固定 MCP Catalog 条目解析到当前 Session Sandbox generation。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit, urlunsplit

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeTurnRequest,
)
from literature_agent.domain.mcp_configuration import McpPolicyRef, McpProfileSelection
from literature_agent.infrastructure.agent.mcp_tools import ResolvedMcpConnection
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
)

_FIXED_SANDBOX_MCP_SERVICES = {
    ("playwright", "0.0.79"): ("playwright", 8931, "/mcp"),
    ("arxiv-search", "0.6.2"): ("arxiv-search", 8932, "/mcp/"),
}


class SandboxMcpBackend(Protocol):
    """Resolver 所需的最小 OpenSandbox 内部能力。"""

    def prepare_mcp_service(self, service_name: str) -> None: ...
    def configure_mcp_service(self, service_name: str, *, allowed_host: str) -> None: ...
    def get_mcp_endpoint(self, port: int) -> tuple[str, dict[str, str], str]: ...


@dataclass(frozen=True, slots=True)
class McpSandboxServerRecipe:
    """不含用户输入的固定镜像进程 recipe。"""

    catalog_id: str
    version: str
    service_name: str
    port: int

    def __post_init__(self) -> None:
        expected = _FIXED_SANDBOX_MCP_SERVICES.get((self.catalog_id, self.version))
        if expected is None or expected[:2] != (self.service_name, self.port):
            raise ValueError("Sandbox MCP recipe 未经平台注册")

    @property
    def mcp_path(self) -> str:
        """返回与固定 Catalog 版本绑定的 Streamable HTTP path。"""
        return _FIXED_SANDBOX_MCP_SERVICES[(self.catalog_id, self.version)][2]

    @property
    def config_hash(self) -> str:
        return McpProfileSelection(
            catalog_id=self.catalog_id,
            version=self.version,
        ).config_hash


class SandboxMcpConnectionResolver:
    """每次按当前 Lease 解析 endpoint，绝不跨 generation 缓存连接。"""

    def __init__(
        self,
        recipes: tuple[McpSandboxServerRecipe, ...],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if len({item.catalog_id for item in recipes}) != len(recipes):
            raise ValueError("Sandbox MCP Catalog recipe 不得重复")
        self._recipes = {item.catalog_id: item for item in recipes}
        self._clock = clock

    async def resolve(
        self,
        request: RuntimeTurnRequest,
        ref: McpPolicyRef,
        lease: SandboxWorkspaceLease,
    ) -> ResolvedMcpConnection:
        recipe = self._recipes.get(ref.catalog_id)
        if recipe is None or ref.version != recipe.version or ref.config_hash != recipe.config_hash:
            raise _error("runtime_mcp_catalog_mismatch", "MCP Catalog 解析结果不匹配")
        record = lease.record
        context = request.context_snapshot
        if (
            record.session_id != request.session_id
            or record.owner_id != context.owner_id
            or record.project_id != context.project_id
            or record.holder_turn_run_id != request.turn_run_id
        ):
            raise _error("runtime_mcp_scope_mismatch", "MCP Tool Turn scope 不匹配")
        if record.status is not SandboxLeaseStatus.ACTIVE or record.expires_at <= self._clock():
            raise _error(
                "runtime_mcp_unavailable",
                "MCP 能力暂时不可用",
                RuntimeErrorKind.TEMPORARY,
            )
        backend = lease.backend
        prepare = getattr(backend, "prepare_mcp_service", None)
        configure = getattr(backend, "configure_mcp_service", None)
        get_endpoint = getattr(backend, "get_mcp_endpoint", None)
        if not callable(prepare) or not callable(configure) or not callable(get_endpoint):
            raise _error("runtime_mcp_catalog_unavailable", "MCP Catalog 条目未安装")
        typed_backend = cast(SandboxMcpBackend, backend)
        try:
            await asyncio.to_thread(
                typed_backend.prepare_mcp_service,
                recipe.service_name,
            )
            endpoint, headers, allowed_host = await asyncio.to_thread(
                typed_backend.get_mcp_endpoint,
                recipe.port,
            )
            await asyncio.to_thread(
                typed_backend.configure_mcp_service,
                recipe.service_name,
                allowed_host=allowed_host,
            )
        except asyncio.CancelledError:
            raise
        except ResearchAgentRuntimeError:
            raise
        except Exception:
            raise _error(
                "runtime_mcp_unavailable",
                "MCP 能力暂时不可用",
                RuntimeErrorKind.TEMPORARY,
            ) from None
        return ResolvedMcpConnection(
            server_name=recipe.catalog_id,
            connection={
                "transport": "streamable_http",
                "url": _mcp_url(endpoint, recipe.mcp_path),
                "headers": dict(headers),
            },
        )


def _mcp_url(endpoint: str, mcp_path: str) -> str:
    """仅接受 OpenSandbox 返回的 HTTP(S) endpoint，并追加固定 MCP path。"""
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise _error(
            "runtime_mcp_unavailable",
            "MCP 能力暂时不可用",
            RuntimeErrorKind.TEMPORARY,
        )
    path = f"{parts.path.rstrip('/')}{mcp_path}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _error(
    code: str,
    safe_message: str,
    kind: RuntimeErrorKind = RuntimeErrorKind.PERMANENT,
) -> ResearchAgentRuntimeError:
    return ResearchAgentRuntimeError(kind=kind, code=code, safe_message=safe_message)


PLATFORM_SANDBOX_MCP_RESOLVER = SandboxMcpConnectionResolver(
    (
        McpSandboxServerRecipe(
            catalog_id="playwright",
            version="0.0.79",
            service_name="playwright",
            port=8931,
        ),
        McpSandboxServerRecipe(
            catalog_id="arxiv-search",
            version="0.6.2",
            service_name="arxiv-search",
            port=8932,
        ),
    )
)
