"""OpenSandbox 0.1.15 到 Deep Agents BaseSandbox 的薄适配。"""

from __future__ import annotations

import asyncio
import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox import SandboxSync
from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import DirectoryListEntry, WriteEntry
from opensandbox.models.sandboxes import NetworkPolicy

from literature_agent.domain.workspace_snapshot import is_workspace_file_path

_PLATFORM_MCP_SERVICES = frozenset({"playwright", "arxiv-search"})
_PLATFORM_MCP_PORTS = frozenset({8931, 8932})
_PLATFORM_BROWSER_PROXY_PORT = 6080
_MCP_ALLOWED_HOST = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
# OpenSandbox API 以十进制整数承载 chmod 的八进制数字，而不是 Python 位掩码值。
_WORKSPACE_DIRECTORY_MODE = 700
_WORKSPACE_FILE_MODE = 600


@dataclass(frozen=True, slots=True, repr=False)
class BrowserWebSocketEndpoint:
    """仅在 Infrastructure 内短暂传递的 OpenSandbox 私有画面 endpoint。"""

    url: str
    headers: dict[str, str]


class OpenSandboxBackend(BaseSandbox):
    """把文件和命令固定到一个已由平台租用的远程 Sandbox。"""

    enable_capture_offload = False

    def __init__(
        self,
        sandbox: Any,
        *,
        command_timeout_seconds: int = 60,
        max_inline_bytes: int = 64 * 1024,
        direct_endpoint_getter: Callable[[int], Any] | None = None,
    ) -> None:
        if command_timeout_seconds <= 0 or max_inline_bytes <= 0:
            raise ValueError("Sandbox 命令超时和输出上限必须为正数")
        self._sandbox = sandbox
        self._command_timeout_seconds = command_timeout_seconds
        self._max_inline_bytes = max_inline_bytes
        self._direct_endpoint_getter = direct_endpoint_getter or sandbox.get_endpoint

    @property
    def id(self) -> str:
        """仅供 Runtime 内部区分 Backend；不得写入 Prompt/Event。"""
        return str(self._sandbox.id)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """在 `/workspace` 中执行，并硬限制单次墙钟和返回字节数。"""
        effective_timeout = min(
            timeout or self._command_timeout_seconds,
            self._command_timeout_seconds,
        )
        # OpenSandbox 0.1.15 的 execd timeout 是 RPC 等待上限，实际验证不会终止
        # 已启动的命令；因此固定镜像内再用 coreutils timeout 约束整个进程组。
        bounded_command = (
            "timeout --kill-after=1s "
            f"{effective_timeout}s sh -lc {shlex.quote(command)}"
        )
        execution = self._sandbox.commands.run(
            bounded_command,
            opts=RunCommandOpts(
                working_directory="/workspace",
                timeout=timedelta(seconds=effective_timeout + 2),
            ),
        )
        output = str(execution)
        encoded = output.encode("utf-8", errors="replace")
        truncated = len(encoded) > self._max_inline_bytes
        if truncated:
            output = encoded[: self._max_inline_bytes].decode("utf-8", errors="ignore")
        return ExecuteResponse(
            output=output,
            exit_code=execution.exit_code,
            truncated=truncated,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传仅接受平台规范化的 `/workspace` 文件路径。"""
        valid = [(path, content) for path, content in files if is_workspace_file_path(path)]
        parents: set[str] = set()
        for path, _ in valid:
            parent = posixpath.dirname(path)
            while parent != "/workspace":
                parents.add(parent)
                parent = posixpath.dirname(parent)
        mkdir_failed = False
        if parents:
            try:
                self._sandbox.files.create_directories(
                    [
                        WriteEntry(path=path, mode=_WORKSPACE_DIRECTORY_MODE)
                        for path in sorted(parents, key=lambda item: (item.count("/"), item))
                    ]
                )
            except Exception:
                mkdir_failed = True
        responses: list[FileUploadResponse] = []
        for path, content in files:
            if not is_workspace_file_path(path):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            if mkdir_failed:
                responses.append(FileUploadResponse(path=path, error="upload_failed"))
                continue
            try:
                self._sandbox.files.write_file(path, content, mode=_WORKSPACE_FILE_MODE)
            except Exception:
                responses.append(FileUploadResponse(path=path, error="upload_failed"))
            else:
                responses.append(FileUploadResponse(path=path))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """下载仅接受平台规范化的 `/workspace` 文件路径。"""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not is_workspace_file_path(path):
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue
            try:
                content = self._sandbox.files.read_bytes(path)
            except Exception:
                responses.append(FileDownloadResponse(path=path, error="download_failed"))
            else:
                responses.append(FileDownloadResponse(path=path, content=content))
        return responses

    def list_workspace_files(self) -> list[tuple[str, str, int]]:
        """列出快照候选并保留 Provider 报告的文件类型以拒绝 symlink/device。"""
        entries = self._sandbox.files.list_directory(
            DirectoryListEntry(path="/workspace", depth=64)
        )
        return [
            (str(item.path), str(item.entry_type or "unknown"), int(item.size))
            for item in entries
            if str(item.path) != "/workspace"
        ]

    def prepare_mcp_service(self, service_name: str) -> None:
        """先以仅 loopback allowlist 启动，满足 endpoint 解析前置条件。"""
        if service_name not in _PLATFORM_MCP_SERVICES:
            raise ValueError("MCP Service 未在平台镜像中注册")
        self._run_mcp_recipe(f"/opt/research-agent/start-mcp-service {service_name} bootstrap")

    def configure_mcp_service(self, service_name: str, *, allowed_host: str) -> None:
        """把 bootstrap 进程一次性收敛到 Provider 的精确 authority。"""
        if service_name not in _PLATFORM_MCP_SERVICES:
            raise ValueError("MCP Service 未在平台镜像中注册")
        if not _MCP_ALLOWED_HOST.fullmatch(allowed_host):
            raise ValueError("MCP Host 不是安全的 Provider authority")
        if ":" in allowed_host and int(allowed_host.rsplit(":", 1)[1]) > 65535:
            raise ValueError("MCP Host 不是安全的 Provider authority")
        self._run_mcp_recipe(
            f"/opt/research-agent/start-mcp-service {service_name} configure {allowed_host}"
        )

    def _run_mcp_recipe(self, command: str) -> None:
        """以固定超时运行已经过平台注册和语法校验的镜像 recipe。"""
        execution = self._sandbox.commands.run(
            command,
            opts=RunCommandOpts(
                working_directory="/workspace",
                timeout=timedelta(seconds=30),
            ),
        )
        if execution.exit_code != 0:
            raise RuntimeError("Sandbox MCP Service 启动失败")

    def prepare_browser_proxy(self) -> None:
        """幂等启动固定 6080→5901 websockify，不接受调用方配置。"""
        execution = self._sandbox.commands.run(
            "/opt/research-agent/start-browser-proxy",
            opts=RunCommandOpts(
                working_directory="/workspace",
                timeout=timedelta(seconds=30),
            ),
        )
        if execution.exit_code != 0:
            raise RuntimeError("Sandbox Browser proxy 启动失败")

    def get_browser_websocket_endpoint(
        self, port: int = _PLATFORM_BROWSER_PROXY_PORT
    ) -> BrowserWebSocketEndpoint:
        """解析固定 websockify server-proxy endpoint；不得持久化或输出。"""
        if port != _PLATFORM_BROWSER_PROXY_PORT:
            raise ValueError("Browser 端口未在固定镜像中注册")
        endpoint = self._sandbox.get_endpoint(port)
        endpoint_url = self._normalize_endpoint(str(endpoint.endpoint))
        parts = urlsplit(endpoint_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or bool(parts.fragment)
        ):
            raise ValueError("OpenSandbox Browser endpoint 非法")
        headers: dict[str, str] = {}
        for key, value in dict(endpoint.headers).items():
            normalized_key = str(key)
            normalized_value = str(value)
            if (
                not normalized_key
                or "\r" in normalized_key
                or "\n" in normalized_key
                or "\r" in normalized_value
                or "\n" in normalized_value
            ):
                raise ValueError("OpenSandbox Browser endpoint header 非法")
            headers[normalized_key] = normalized_value
        websocket_url = urlunsplit(
            (
                "wss" if parts.scheme == "https" else "ws",
                parts.netloc,
                parts.path,
                parts.query,
                "",
            )
        )
        return BrowserWebSocketEndpoint(websocket_url, headers)

    def get_mcp_endpoint(self, port: int) -> tuple[str, dict[str, str], str]:
        """解析当前 Sandbox generation 的私有 endpoint；调用者不得持久化。"""
        if port not in _PLATFORM_MCP_PORTS:
            raise ValueError("MCP 端口未在平台镜像中注册")
        endpoint = self._sandbox.get_endpoint(port)
        direct_endpoint = self._direct_endpoint_getter(port)
        endpoint_url = self._normalize_endpoint(str(endpoint.endpoint))
        direct_url = self._normalize_endpoint(str(direct_endpoint.endpoint))
        allowed_host = self._endpoint_authority(direct_url)
        return endpoint_url, dict(endpoint.headers), allowed_host

    def _normalize_endpoint(self, endpoint: str) -> str:
        if "://" in endpoint:
            return endpoint
        protocol = str(self._sandbox.connection_config.protocol)
        if endpoint.startswith("/"):
            domain = str(self._sandbox.connection_config.domain)
            return f"{protocol}://{domain}{endpoint}"
        return f"{protocol}://{endpoint}"

    @staticmethod
    def _endpoint_authority(endpoint: str) -> str:
        parts = urlsplit(endpoint)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("OpenSandbox MCP endpoint 非法")
        return parts.netloc

    def close(self) -> None:
        """只关闭本地 HTTP 资源；Session Lease 的远端实例继续存在。"""
        self._sandbox.close()


@dataclass(slots=True)
class OpenSandboxProvider:
    """平台维护的固定 OpenSandbox 0.1.15 生命周期 Adapter。"""

    domain: str
    protocol: str = "http"
    api_key: str | None = field(default=None, repr=False)
    use_server_proxy: bool = True
    sandbox_cls: Any = field(default=SandboxSync, repr=False)

    def _connection(self) -> ConnectionConfigSync:
        return ConnectionConfigSync(
            domain=self.domain,
            protocol=self.protocol,
            api_key=self.api_key,
            use_server_proxy=self.use_server_proxy,
            disable_metrics=True,
        )

    async def create(
        self,
        *,
        image_ref: str,
        ttl_seconds: int,
        cpu: int,
        memory_mib: int,
        network_enabled: bool,
        metadata: dict[str, str],
    ) -> OpenSandboxBackend:
        """创建固定资源、空环境且 default-deny 的 Sandbox。"""
        if network_enabled:
            raise ValueError("Slice 7.1 不允许开启 Sandbox 网络")
        sandbox = await asyncio.to_thread(
            self.sandbox_cls.create,
            image_ref,
            timeout=timedelta(seconds=ttl_seconds),
            env={},
            entrypoint=["/entrypoint"],
            metadata=dict(metadata),
            resource={"cpu": str(cpu), "memory": f"{memory_mib}Mi"},
            network_policy=NetworkPolicy(defaultAction="deny", egress=[]),
            volumes=[],
            connection_config=self._connection(),
        )
        return OpenSandboxBackend(
            sandbox,
            direct_endpoint_getter=lambda port: self._get_direct_endpoint(sandbox.id, port),
        )

    async def connect(self, sandbox_id: str) -> OpenSandboxBackend:
        sandbox = await asyncio.to_thread(
            self.sandbox_cls.connect,
            sandbox_id,
            connection_config=self._connection(),
        )
        return OpenSandboxBackend(
            sandbox,
            direct_endpoint_getter=lambda port: self._get_direct_endpoint(sandbox.id, port),
        )

    def _get_direct_endpoint(self, sandbox_id: str, port: int) -> Any:
        connection = self._connection().model_copy(update={"use_server_proxy": False})
        sandbox = self.sandbox_cls.connect(
            sandbox_id,
            connection_config=connection,
            skip_health_check=True,
        )
        try:
            return sandbox.get_endpoint(port)
        finally:
            sandbox.close()

    async def renew(self, sandbox_id: str, *, ttl_seconds: int) -> None:
        sandbox = await asyncio.to_thread(
            self.sandbox_cls.connect,
            sandbox_id,
            connection_config=self._connection(),
        )
        try:
            await asyncio.to_thread(sandbox.renew, timedelta(seconds=ttl_seconds))
        finally:
            await asyncio.to_thread(sandbox.close)

    async def destroy(self, sandbox_id: str) -> None:
        try:
            sandbox = await asyncio.to_thread(
                self.sandbox_cls.connect,
                sandbox_id,
                connection_config=self._connection(),
                skip_health_check=True,
            )
            await asyncio.to_thread(sandbox.destroy)
        except SandboxApiException as exc:
            if exc.status_code != 404:
                raise

    async def get_browser_websocket_target(
        self,
        sandbox_id: str,
        *,
        port: int = _PLATFORM_BROWSER_PROXY_PORT,
    ) -> BrowserWebSocketEndpoint:
        """在既有 Sandbox 启动固定 proxy，并解析带 Provider header 的 WS endpoint。"""
        if port != _PLATFORM_BROWSER_PROXY_PORT:
            raise ValueError("Browser 端口未在固定镜像中注册")
        backend = await self.connect(sandbox_id)
        try:
            await asyncio.to_thread(backend.prepare_browser_proxy)
            return await asyncio.to_thread(
                backend.get_browser_websocket_endpoint,
                port,
            )
        finally:
            await asyncio.to_thread(backend.close)
