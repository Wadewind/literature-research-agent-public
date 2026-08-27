"""OpenSandbox → Deep Agents BaseSandbox 的完全离线薄适配测试。"""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import WriteEntry

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

    def create_directories(self, entries) -> None:
        self.created_directories.append(entries)

    def write_file(self, path: str, data: bytes, **kwargs) -> None:
        del kwargs
        self.data[path] = data

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

    def get_endpoint(self, port: int):
        assert port == 8931
        return type(
            "Endpoint",
            (),
            {
                "endpoint": "http://sandbox-proxy.invalid/private",
                "headers": {"X-Sandbox-Route": "opaque"},
            },
        )()


def test_execute_is_workspace_scoped_timed_and_output_bounded() -> None:
    sandbox = _Sandbox()
    backend = OpenSandboxBackend(sandbox, command_timeout_seconds=60, max_inline_bytes=64 * 1024)

    result = backend.execute("python3 -c 'print(1)'")

    assert result.exit_code == 0
    assert result.truncated is True
    assert len(result.output.encode()) <= 64 * 1024
    _, options = sandbox.commands.calls[0]
    assert options.working_directory == "/workspace"
    assert options.timeout == timedelta(seconds=60)
    assert "sandbox-private-id" not in result.output


def test_upload_and_download_only_accept_workspace_paths() -> None:
    sandbox = _Sandbox()
    backend = OpenSandboxBackend(sandbox)

    uploaded = backend.upload_files([("/workspace/note.md", b"note"), ("/etc/passwd", b"bad")])
    downloaded = backend.download_files(["/workspace/note.md", "/etc/passwd"])

    assert uploaded[0].error is None
    assert uploaded[1].error == "invalid_path"
    assert downloaded[0].content == b"note"
    assert downloaded[1].error == "invalid_path"


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


def test_platform_mcp_service_and_endpoint_are_fixed() -> None:
    sandbox = _Sandbox()
    sandbox.commands.run = lambda command, *, opts: (
        sandbox.commands.calls.append((command, opts)) or _Execution("ready")
    )
    backend = OpenSandboxBackend(sandbox)

    backend.prepare_mcp_service("playwright")
    endpoint, headers = backend.get_mcp_endpoint(8931)
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


def test_platform_mcp_service_rejects_unregistered_recipe_and_port() -> None:
    backend = OpenSandboxBackend(_Sandbox())

    with pytest.raises(ValueError, match="MCP Service"):
        backend.prepare_mcp_service("user-command")
    with pytest.raises(ValueError, match="MCP Host"):
        backend.configure_mcp_service("playwright", allowed_host="bad;host")
    with pytest.raises(ValueError, match="MCP 端口"):
        backend.get_mcp_endpoint(9999)


async def test_provider_creation_is_fixed_resource_default_deny_and_secret_free(
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
        network_enabled=False,
        metadata={"session_id": "session-1"},
    )

    assert backend.id == "sandbox-private-id"
    assert calls[0]["image"] == "research@sha256:pinned"
    assert calls[0]["resource"] == {"cpu": "1", "memory": "2048Mi"}
    assert calls[0]["network_policy"].default_action == "deny"
    assert calls[0]["network_policy"].egress == []
    assert calls[0]["env"] == {}
    assert calls[0]["entrypoint"] == ["/entrypoint"]
    assert "provider-secret" not in repr(provider)
