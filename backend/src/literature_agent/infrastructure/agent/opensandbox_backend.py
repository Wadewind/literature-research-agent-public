"""OpenSandbox 0.1.15 到 Deep Agents BaseSandbox 的薄适配。"""

from __future__ import annotations

import asyncio
import posixpath
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox import SandboxSync
from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import DirectoryListEntry, WriteEntry
from opensandbox.models.sandboxes import NetworkPolicy

from literature_agent.domain.workspace_snapshot import is_workspace_file_path


class OpenSandboxBackend(BaseSandbox):
    """把文件和命令固定到一个已由平台租用的远程 Sandbox。"""

    enable_capture_offload = False

    def __init__(
        self,
        sandbox: Any,
        *,
        command_timeout_seconds: int = 60,
        max_inline_bytes: int = 64 * 1024,
    ) -> None:
        if command_timeout_seconds <= 0 or max_inline_bytes <= 0:
            raise ValueError("Sandbox 命令超时和输出上限必须为正数")
        self._sandbox = sandbox
        self._command_timeout_seconds = command_timeout_seconds
        self._max_inline_bytes = max_inline_bytes

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
        execution = self._sandbox.commands.run(
            command,
            opts=RunCommandOpts(
                working_directory="/workspace",
                timeout=timedelta(seconds=effective_timeout),
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
                        WriteEntry(path=path, mode=0o700)
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
                self._sandbox.files.write_file(path, content, mode=0o600)
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
            metadata=dict(metadata),
            resource={"cpu": str(cpu), "memory": f"{memory_mib}Mi"},
            network_policy=NetworkPolicy(defaultAction="deny", egress=[]),
            volumes=[],
            connection_config=self._connection(),
        )
        return OpenSandboxBackend(sandbox)

    async def connect(self, sandbox_id: str) -> OpenSandboxBackend:
        sandbox = await asyncio.to_thread(
            self.sandbox_cls.connect,
            sandbox_id,
            connection_config=self._connection(),
        )
        return OpenSandboxBackend(sandbox)

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
        sandbox = await asyncio.to_thread(
            self.sandbox_cls.connect,
            sandbox_id,
            connection_config=self._connection(),
            skip_health_check=True,
        )
        await asyncio.to_thread(sandbox.destroy)
