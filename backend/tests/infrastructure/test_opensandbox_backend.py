"""OpenSandbox → Deep Agents BaseSandbox 的完全离线薄适配测试。"""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from opensandbox.exceptions import SandboxApiException, SandboxError
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import WriteEntry

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
)
from literature_agent.domain.agent_network import RESEARCH_PUBLIC_EGRESS_PROFILE
from literature_agent.infrastructure.agent.opensandbox_backend import (
    OpenSandboxBackend,
    OpenSandboxProvider,
)


@dataclass
class _Logs:
    stderr: list[object] = field(default_factory=list)


@dataclass
class _Execution:
    text: str
    exit_code: int | None = 0
    logs: _Logs = field(default_factory=_Logs)
    error: object | None = None


class _Commands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RunCommandOpts]] = []

    def run(self, command: str, *, opts):
        self.calls.append((command, opts))
        return _Execution("x" * (70 * 1024))


class _Files:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.created_directories: list[list[WriteEntry]] = []
        self.write_options: dict[str, dict[str, object]] = {}

    def create_directories(self, entries) -> None:
        self.created_directories.append(entries)

    def write_file(self, path: str, data: bytes, **kwargs) -> None:
        self.data[path] = data
        self.write_options[path] = kwargs

    def read_bytes(self, path: str) -> bytes:
        return self.data[path]

    def list_directory(self, entry):
        del entry
        return []


class _Sandbox:
    id = "sandbox-private-id"

    def __init__(self) -> None:
        self.commands = _Commands()
        self.files = _Files()
        self.connection_config = type(
            "ConnectionConfig",
            (),
            {"protocol": "http", "domain": "sandbox-server.invalid:8080"},
        )()
        self.endpoint = "http://sandbox-proxy.invalid/private"

    def get_endpoint(self, port: int):
        assert port in {6080, 8931}
        return type(
            "Endpoint",
            (),
            {
                "endpoint": self.endpoint,
                "headers": {"X-Sandbox-Route": "opaque"},
            },
        )()

    def close(self) -> None:
        return None


def test_execute_is_workspace_scoped_timed_and_output_bounded() -> None:
    sandbox = _Sandbox()
    backend = OpenSandboxBackend(sandbox, command_timeout_seconds=60, max_inline_bytes=64 * 1024)

    result = backend.execute("python3 -c 'print(1)'")

    assert result.exit_code == 0
    assert result.truncated is True
    assert len(result.output.encode()) <= 64 * 1024
    executed, options = sandbox.commands.calls[0]
    assert executed.startswith("timeout --kill-after=1s 60s sh -c ")
    assert "python3 -c" in executed
    assert options.working_directory == "/workspace"
    assert options.timeout == timedelta(seconds=62)
    assert "sandbox-private-id" not in result.output


def test_ensure_workspace_layout_creates_formal_output_directory() -> None:
    sandbox = _Sandbox()
    backend = OpenSandboxBackend(sandbox)

    backend.ensure_workspace_layout()

    assert len(sandbox.files.created_directories) == 1
    entries = sandbox.files.created_directories[0]
    assert [(item.path, item.mode) for item in entries] == [
        ("/workspace/outputs", 700)
    ]


def test_upload_and_download_only_accept_workspace_paths() -> None:
    sandbox = _Sandbox()
    backend = OpenSandboxBackend(sandbox)

    uploaded = backend.upload_files([("/workspace/note.md", b"note"), ("/etc/passwd", b"bad")])
    downloaded = backend.download_files(["/workspace/note.md", "/etc/passwd"])

    assert uploaded[0].error is None
    assert uploaded[1].error == "invalid_path"
    assert downloaded[0].content == b"note"
    assert downloaded[1].error == "invalid_path"
    assert sandbox.files.write_options["/workspace/note.md"]["mode"] == 600


def test_upload_creates_nested_parent_directories_before_write() -> None:
    sandbox = _Sandbox()
    backend = OpenSandboxBackend(sandbox)

    uploaded = backend.upload_files(
        [
            ("/workspace/nested/a.txt", b"a"),
            ("/workspace/nested/deeper/b.txt", b"b"),
        ]
    )

    assert all(item.error is None for item in uploaded)
    assert len(sandbox.files.created_directories) == 1
    entries = sandbox.files.created_directories[0]
    assert [item.path for item in entries] == [
        "/workspace/nested",
        "/workspace/nested/deeper",
    ]
    assert all(item.mode == 700 for item in entries)
    assert all(options["mode"] == 600 for options in sandbox.files.write_options.values())


def test_platform_mcp_service_and_endpoint_are_fixed() -> None:
    sandbox = _Sandbox()
    sandbox.commands.run = lambda command, *, opts: (
        sandbox.commands.calls.append((command, opts)) or _Execution("ready")
    )
    backend = OpenSandboxBackend(sandbox)

    backend.prepare_mcp_service("playwright")
    endpoint, headers, allowed_host = backend.get_mcp_endpoint(8931)
    backend.configure_mcp_service("playwright", allowed_host="sandbox-proxy.invalid")

    assert [call[0] for call in sandbox.commands.calls] == [
        "/opt/research-agent/start-mcp-service playwright bootstrap",
        ("/opt/research-agent/start-mcp-service playwright configure sandbox-proxy.invalid"),
    ]
    assert all(
        options.working_directory == "/workspace" and options.timeout == timedelta(seconds=30)
        for _, options in sandbox.commands.calls
    )
    assert endpoint == "http://sandbox-proxy.invalid/private"
    assert headers == {"X-Sandbox-Route": "opaque"}
    assert allowed_host == "sandbox-proxy.invalid"


def test_browser_proxy_recipe_and_websocket_endpoint_are_fixed() -> None:
    sandbox = _Sandbox()
    sandbox.commands.run = lambda command, *, opts: (
        sandbox.commands.calls.append((command, opts)) or _Execution("ready")
    )
    backend = OpenSandboxBackend(sandbox)

    backend.prepare_browser_proxy()
    endpoint = backend.get_browser_websocket_endpoint(6080)

    assert sandbox.commands.calls[0][0] == "/opt/research-agent/start-browser-proxy"
    assert sandbox.commands.calls[0][1].timeout == timedelta(seconds=30)
    assert endpoint.url == "ws://sandbox-proxy.invalid/private"
    assert endpoint.headers == {"X-Sandbox-Route": "opaque"}
    assert "opaque" not in repr(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://sandbox-proxy.invalid/private",
        "http://user@sandbox-proxy.invalid/private",
        "http://sandbox-proxy.invalid/private#fragment",
    ],
)
def test_browser_websocket_endpoint_rejects_unsafe_urls(endpoint: str) -> None:
    sandbox = _Sandbox()
    sandbox.endpoint = endpoint
    backend = OpenSandboxBackend(sandbox)

    with pytest.raises(ValueError, match="Browser endpoint"):
        backend.get_browser_websocket_endpoint(6080)


def test_browser_websocket_endpoint_rejects_unregistered_port() -> None:
    backend = OpenSandboxBackend(_Sandbox())

    with pytest.raises(ValueError, match="Browser 端口"):
        backend.get_browser_websocket_endpoint(5901)


def test_mcp_endpoint_adds_provider_protocol_when_sdk_omits_scheme() -> None:
    sandbox = _Sandbox()
    sandbox.endpoint = "127.0.0.1:8080/v1/sandboxes/private/proxy/8931"
    backend = OpenSandboxBackend(sandbox)

    endpoint, _, allowed_host = backend.get_mcp_endpoint(8931)

    assert endpoint == "http://127.0.0.1:8080/v1/sandboxes/private/proxy/8931"
    assert allowed_host == "127.0.0.1:8080"


def test_mcp_endpoint_uses_exact_direct_authority_for_proxy_host_allowlist() -> None:
    sandbox = _Sandbox()
    direct_endpoint = type(
        "Endpoint",
        (),
        {"endpoint": "127.0.0.1:54178/proxy/8931", "headers": {"secret": "hidden"}},
    )()
    backend = OpenSandboxBackend(
        sandbox,
        direct_endpoint_getter=lambda port: direct_endpoint if port == 8931 else None,
    )

    endpoint, headers, allowed_host = backend.get_mcp_endpoint(8931)

    assert endpoint == "http://sandbox-proxy.invalid/private"
    assert headers == {"X-Sandbox-Route": "opaque"}
    assert allowed_host == "127.0.0.1:54178"


def test_mcp_endpoint_rejects_direct_authority_with_userinfo() -> None:
    sandbox = _Sandbox()
    direct_endpoint = type(
        "Endpoint",
        (),
        {"endpoint": "http://user@127.0.0.1:54178/proxy/8931", "headers": {}},
    )()
    backend = OpenSandboxBackend(sandbox, direct_endpoint_getter=lambda _port: direct_endpoint)

    with pytest.raises(ValueError, match="MCP endpoint"):
        backend.get_mcp_endpoint(8931)


def test_platform_mcp_service_rejects_unregistered_recipe_and_port() -> None:
    backend = OpenSandboxBackend(_Sandbox())

    with pytest.raises(ValueError, match="MCP Service"):
        backend.prepare_mcp_service("user-command")
    with pytest.raises(ValueError, match="MCP Host"):
        backend.configure_mcp_service("playwright", allowed_host="bad;host")
    with pytest.raises(ValueError, match="MCP 端口"):
        backend.get_mcp_endpoint(9999)


@pytest.mark.asyncio
async def test_provider_exposes_only_fixed_browser_websocket_endpoint(
    monkeypatch,
) -> None:
    async def immediate_to_thread(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)
    sandbox = _Sandbox()
    sandbox.endpoint = "https://sandbox.internal/proxy/6080"

    class _SandboxFactory:
        @classmethod
        def connect(cls, sandbox_id, **kwargs):
            assert sandbox_id == "opaque"
            assert kwargs["connection_config"].use_server_proxy is True
            return sandbox

    provider = OpenSandboxProvider(
        domain="sandbox.internal",
        sandbox_cls=_SandboxFactory,
    )
    target = await provider.get_browser_websocket_target("opaque", port=6080)

    assert target.url == "wss://sandbox.internal/proxy/6080"
    assert target.headers == {"X-Sandbox-Route": "opaque"}
    assert sandbox.commands.calls[0][0] == "/opt/research-agent/start-browser-proxy"
    with pytest.raises(ValueError, match="Browser 端口"):
        await provider.get_browser_websocket_target("opaque", port=5901)


async def test_provider_creation_uses_fixed_public_egress_and_is_secret_free(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    async def immediate_to_thread(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)

    class _SandboxFactory:
        @classmethod
        def create(cls, image, **kwargs):
            calls.append({"image": image, **kwargs})
            return _Sandbox()

    provider = OpenSandboxProvider(
        domain="sandbox.internal:8080",
        protocol="http",
        api_key="provider-secret",
        sandbox_cls=_SandboxFactory,
    )
    backend = await provider.create(
        image_ref="research@sha256:pinned",
        ttl_seconds=600,
        cpu=1,
        memory_mib=2048,
        network_enabled=True,
        network_profile_id=RESEARCH_PUBLIC_EGRESS_PROFILE.profile_id,
        network_profile_version=RESEARCH_PUBLIC_EGRESS_PROFILE.version,
        network_profile_hash=RESEARCH_PUBLIC_EGRESS_PROFILE.profile_hash,
        metadata={"session_id": "session-1"},
    )

    assert backend.id == "sandbox-private-id"
    assert calls[0]["image"] == "research@sha256:pinned"
    assert calls[0]["resource"] == {"cpu": "1", "memory": "2048Mi"}
    assert calls[0]["network_policy"].default_action == "allow"
    assert calls[0]["network_policy"].egress
    assert all(rule.action == "deny" for rule in calls[0]["network_policy"].egress)
    assert "127.0.0.0/8" not in {
        rule.target for rule in calls[0]["network_policy"].egress
    }
    assert calls[0]["env"] == {}
    assert calls[0]["entrypoint"] == ["/entrypoint"]
    assert "provider-secret" not in repr(provider)


@pytest.mark.parametrize(
    "metadata",
    [
        {"network_profile_hash": "a" * 64},
        {"bad key": "value"},
        {"opensandbox.io/system": "value"},
        {"key": "-invalid"},
    ],
)
async def test_provider_rejects_invalid_metadata_before_sdk_call(
    monkeypatch,
    metadata: dict[str, str],
) -> None:
    async def immediate_to_thread(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)
    calls = 0

    class _SandboxFactory:
        @classmethod
        def create(cls, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return _Sandbox()

    provider = OpenSandboxProvider(
        domain="sandbox.internal:8080",
        protocol="http",
        sandbox_cls=_SandboxFactory,
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await provider.create(
            image_ref="research@sha256:pinned",
            ttl_seconds=600,
            cpu=1,
            memory_mib=2048,
            network_enabled=False,
            metadata=metadata,
        )

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_sandbox_metadata_invalid"
    assert calls == 0


async def test_provider_normalizes_sdk_invalid_metadata_as_permanent(monkeypatch) -> None:
    async def immediate_to_thread(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)

    class _SandboxFactory:
        @classmethod
        def create(cls, *_args, **_kwargs):
            raise SandboxApiException(
                "metadata rejected",
                status_code=400,
                error=SandboxError(
                    "SANDBOX::INVALID_METADATA_LABEL",
                    "internal provider detail",
                ),
            )

    provider = OpenSandboxProvider(
        domain="sandbox.internal:8080",
        protocol="http",
        sandbox_cls=_SandboxFactory,
    )

    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        await provider.create(
            image_ref="research@sha256:pinned",
            ttl_seconds=600,
            cpu=1,
            memory_mib=2048,
            network_enabled=False,
            metadata={"session_id": "session-1"},
        )

    assert exc_info.value.kind is RuntimeErrorKind.PERMANENT
    assert exc_info.value.code == "runtime_sandbox_metadata_invalid"
    assert "internal provider detail" not in exc_info.value.safe_message


async def test_provider_rejects_unknown_or_drifted_network_profile(monkeypatch) -> None:
    async def immediate_to_thread(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)
    provider = OpenSandboxProvider(
        domain="sandbox.internal:8080",
        protocol="http",
        sandbox_cls=type("Factory", (), {"create": classmethod(lambda cls, *a, **k: _Sandbox())}),
    )

    with pytest.raises(ValueError, match="Network Profile"):
        await provider.create(
            image_ref="research@sha256:pinned",
            ttl_seconds=600,
            cpu=1,
            memory_mib=2048,
            network_enabled=True,
            network_profile_id=RESEARCH_PUBLIC_EGRESS_PROFILE.profile_id,
            network_profile_version=RESEARCH_PUBLIC_EGRESS_PROFILE.version,
            network_profile_hash="0" * 64,
            metadata={"session_id": "session-1"},
        )


async def test_provider_destroy_treats_missing_resource_as_idempotent(monkeypatch) -> None:
    async def immediate_to_thread(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)

    class _MissingSandboxFactory:
        @classmethod
        def connect(cls, *_args, **_kwargs):
            raise SandboxApiException("missing-private-id", status_code=404)

    provider = OpenSandboxProvider(
        domain="sandbox.internal:8080",
        sandbox_cls=_MissingSandboxFactory,
    )

    await provider.destroy("missing-private-id")


async def test_provider_destroy_preserves_non_not_found_failures(monkeypatch) -> None:
    async def immediate_to_thread(call, *args, **kwargs):
        return call(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)

    class _UnavailableSandboxFactory:
        @classmethod
        def connect(cls, *_args, **_kwargs):
            raise SandboxApiException("unavailable-private-id", status_code=503)

    provider = OpenSandboxProvider(
        domain="sandbox.internal:8080",
        sandbox_cls=_UnavailableSandboxFactory,
    )

    with pytest.raises(SandboxApiException) as exc_info:
        await provider.destroy("unavailable-private-id")
    assert exc_info.value.status_code == 503
