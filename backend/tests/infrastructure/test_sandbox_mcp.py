"""Session Sandbox 内固定 MCP Server 的连接解析测试。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeTurnRequest,
)
from literature_agent.domain.mcp_configuration import (
    McpPolicyRef,
    McpPolicyToolRef,
    McpProfileSelection,
)
from literature_agent.infrastructure.agent.sandbox_mcp import (
    McpSandboxServerRecipe,
    SandboxMcpConnectionResolver,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseRecord,
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
)


class _Backend:
    def __init__(self, endpoint: str, header: str) -> None:
        self.endpoint = endpoint
        self.header = header
        self.events: list[str] = []

    def prepare_mcp_service(self, service_name: str) -> None:
        self.events.append(f"prepare:{service_name}")

    def configure_mcp_service(self, service_name: str, *, allowed_host: str) -> None:
        self.events.append(f"configure:{service_name}:{allowed_host}")

    def get_mcp_endpoint(self, port: int) -> tuple[str, dict[str, str], str]:
        assert port == 8932
        assert self.events == ["prepare:arxiv-search"]
        self.events.append("endpoint:8932")
        return self.endpoint, {"X-Sandbox-Route": self.header}, "sandbox-internal:49152"


class _FailingBackend(_Backend):
    def __init__(self, *, fail_on: str) -> None:
        super().__init__("http://must-not-leak.invalid/private", "secret-header")
        self.fail_on = fail_on

    def prepare_mcp_service(self, service_name: str) -> None:
        if self.fail_on == "prepare":
            raise RuntimeError("secret bootstrap command")
        super().prepare_mcp_service(service_name)

    def configure_mcp_service(self, service_name: str, *, allowed_host: str) -> None:
        if self.fail_on == "configure":
            raise RuntimeError(f"secret command endpoint: {allowed_host}")
        super().configure_mcp_service(service_name, allowed_host=allowed_host)

    def get_mcp_endpoint(self, port: int) -> tuple[str, dict[str, str], str]:
        if self.fail_on == "endpoint":
            raise RuntimeError("secret endpoint/header")
        return super().get_mcp_endpoint(port)


def _request() -> RuntimeTurnRequest:
    return cast(
        RuntimeTurnRequest,
        SimpleNamespace(
            session_id="session-1",
            turn_run_id="turn-1",
            context_snapshot=SimpleNamespace(owner_id="owner-1", project_id="project-1"),
        ),
    )


def _ref(*, version: str = "0.6.2", config_hash: str | None = None) -> McpPolicyRef:
    selection = McpProfileSelection(catalog_id="arxiv-search", version=version)
    return McpPolicyRef(
        profile_id="profile-1",
        profile_revision=1,
        catalog_id="arxiv-search",
        version=version,
        config_hash=config_hash or selection.config_hash,
        tools=(McpPolicyToolRef("arxiv-search_search_papers", "a" * 64),),
    )


def _lease(
    backend: _Backend,
    *,
    generation: int = 1,
    owner_id: str = "owner-1",
    project_id: str = "project-1",
    holder_turn_run_id: str = "turn-1",
    status: SandboxLeaseStatus = SandboxLeaseStatus.ACTIVE,
    expires_in: timedelta = timedelta(minutes=10),
) -> SandboxWorkspaceLease:
    now = datetime.now(UTC)
    return SandboxWorkspaceLease(
        record=SandboxLeaseRecord(
            session_id="session-1",
            owner_id=owner_id,
            project_id=project_id,
            holder_turn_run_id=holder_turn_run_id,
            sandbox_id=f"sandbox-{generation}",
            image_ref="image@sha256:fixed",
            generation=generation,
            fencing_token=generation,
            status=status,
            generation_started_at=now,
            expires_at=now + expires_in,
            updated_at=now,
        ),
        backend=backend,
    )


@pytest.mark.asyncio
async def test_resolver_starts_fixed_service_and_never_caches_old_generation() -> None:
    resolver = SandboxMcpConnectionResolver(
        (
            McpSandboxServerRecipe(
                catalog_id="arxiv-search",
                version="0.6.2",
                service_name="arxiv-search",
                port=8932,
            ),
        )
    )
    first_backend = _Backend("http://sandbox-one.invalid/private", "first-secret")
    second_backend = _Backend("http://sandbox-two.invalid/private", "second-secret")

    first = await resolver.resolve(_request(), _ref(), _lease(first_backend))
    second = await resolver.resolve(_request(), _ref(), _lease(second_backend, generation=2))

    assert first.connection == {
        "transport": "streamable_http",
        "url": "http://sandbox-one.invalid/private/mcp/",
        "headers": {"X-Sandbox-Route": "first-secret"},
    }
    assert second.connection["url"] == "http://sandbox-two.invalid/private/mcp/"
    assert first_backend.events == [
        "prepare:arxiv-search",
        "endpoint:8932",
        "configure:arxiv-search:sandbox-internal:49152",
    ]
    assert second_backend.events == [
        "prepare:arxiv-search",
        "endpoint:8932",
        "configure:arxiv-search:sandbox-internal:49152",
    ]
    assert "first-secret" not in repr(first)
    assert "sandbox-one" not in repr(first)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("turn_request", "ref", "expected_code"),
    [
        (_request(), _ref(version="9.9.9"), "runtime_mcp_catalog_mismatch"),
        (_request(), _ref(config_hash="f" * 64), "runtime_mcp_catalog_mismatch"),
        (
            SimpleNamespace(
                session_id="other-session",
                turn_run_id="turn-1",
                context_snapshot=SimpleNamespace(owner_id="owner-1", project_id="project-1"),
            ),
            _ref(),
            "runtime_mcp_scope_mismatch",
        ),
    ],
)
async def test_resolver_fails_closed_before_starting_service(
    turn_request, ref, expected_code: str
) -> None:
    backend = _Backend("http://sandbox.invalid/private", "secret")
    resolver = SandboxMcpConnectionResolver(
        (
            McpSandboxServerRecipe(
                catalog_id="arxiv-search",
                version="0.6.2",
                service_name="arxiv-search",
                port=8932,
            ),
        )
    )

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await resolver.resolve(turn_request, ref, _lease(backend))

    assert caught.value.code == expected_code
    assert backend.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lease_kwargs", "expected_code"),
    [
        ({"owner_id": "other-owner"}, "runtime_mcp_scope_mismatch"),
        ({"project_id": "other-project"}, "runtime_mcp_scope_mismatch"),
        ({"holder_turn_run_id": "other-turn"}, "runtime_mcp_scope_mismatch"),
        ({"status": SandboxLeaseStatus.DIRTY}, "runtime_mcp_unavailable"),
        ({"expires_in": timedelta(seconds=-1)}, "runtime_mcp_unavailable"),
    ],
)
async def test_resolver_rejects_wrong_lease_scope_or_health(
    lease_kwargs: dict[str, object], expected_code: str
) -> None:
    backend = _Backend("http://sandbox.invalid/private", "secret")
    resolver = SandboxMcpConnectionResolver(
        (
            McpSandboxServerRecipe(
                catalog_id="arxiv-search",
                version="0.6.2",
                service_name="arxiv-search",
                port=8932,
            ),
        )
    )

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await resolver.resolve(_request(), _ref(), _lease(backend, **lease_kwargs))  # type: ignore[arg-type]

    assert caught.value.code == expected_code
    assert backend.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on", ["prepare", "endpoint", "configure"])
async def test_resolver_filters_provider_and_recipe_errors(fail_on: str) -> None:
    backend = _FailingBackend(fail_on=fail_on)
    resolver = SandboxMcpConnectionResolver(
        (
            McpSandboxServerRecipe(
                catalog_id="arxiv-search",
                version="0.6.2",
                service_name="arxiv-search",
                port=8932,
            ),
        )
    )

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await resolver.resolve(_request(), _ref(), _lease(backend))

    assert caught.value.code == "runtime_mcp_unavailable"
    assert caught.value.safe_message == "MCP 能力暂时不可用"
    assert "secret" not in str(caught.value)
    assert "must-not-leak" not in repr(caught.value)
