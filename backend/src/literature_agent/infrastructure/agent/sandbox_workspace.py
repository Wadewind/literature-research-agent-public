"""Session 级 Sandbox Lease 与 WorkspaceSnapshot 协调。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from literature_agent.application.ports.research_agent_runtime import RuntimeTurnRequest
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.workspace_snapshot import (
    WORKSPACE_MAX_FILE_BYTES,
    WORKSPACE_MAX_FILES,
    WORKSPACE_MAX_TOTAL_BYTES,
    WorkspaceFile,
    WorkspaceSnapshot,
    WorkspaceSnapshotStatus,
    create_workspace_snapshot,
    is_workspace_file_path,
)


class SandboxLeaseStatus(StrEnum):
    """物理 Workspace 的内部可用性；不进入公开 API。"""

    ACTIVE = "active"
    DIRTY = "dirty"


@dataclass(frozen=True, slots=True)
class SandboxLeaseRecord:
    """SDK-neutral 的 Session Lease 持久控制事实。"""

    session_id: str
    owner_id: str
    project_id: str
    holder_turn_run_id: str
    sandbox_id: str
    image_ref: str
    generation: int
    fencing_token: int
    status: SandboxLeaseStatus
    generation_started_at: datetime
    expires_at: datetime
    updated_at: datetime

    def as_dirty(self) -> SandboxLeaseRecord:
        """取消/失败后禁止当前物理环境继续承载新 Turn。"""
        return replace(self, status=SandboxLeaseStatus.DIRTY, updated_at=datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class SandboxWorkspaceLease:
    """一次 Runtime 调用持有的 fenced Backend。"""

    record: SandboxLeaseRecord
    backend: Any


class SandboxWorkspaceRepository(Protocol):
    """Lease 与 Snapshot 的最小持久化操作；每个方法自行使用短事务。"""

    async def get_lease(self, session_id: str) -> SandboxLeaseRecord | None: ...
    async def replace_lease(
        self,
        value: SandboxLeaseRecord,
        *,
        expected_fencing_token: int | None,
    ) -> bool: ...
    async def mark_dirty(
        self, session_id: str, generation: int, fencing_token: int
    ) -> bool: ...
    async def latest_snapshot(self, session_id: str) -> WorkspaceSnapshot | None: ...
    async def snapshot_for_turn(self, turn_run_id: str) -> WorkspaceSnapshot | None: ...
    async def stage_snapshot(
        self,
        value: WorkspaceSnapshot,
        *,
        lease_generation: int,
        fencing_token: int,
    ) -> bool: ...


class SandboxProvider(Protocol):
    """Provider 生命周期 Port；具体 OpenSandbox SDK 只存在于 Adapter。"""

    async def create(
        self,
        *,
        image_ref: str,
        ttl_seconds: int,
        cpu: int,
        memory_mib: int,
        network_enabled: bool,
        metadata: dict[str, str],
    ) -> Any: ...
    async def connect(self, sandbox_id: str) -> Any: ...
    async def renew(self, sandbox_id: str, *, ttl_seconds: int) -> None: ...
    async def destroy(self, sandbox_id: str) -> None: ...


class SandboxWorkspaceManager:
    """把 Provider I/O 与短事务 CAS 分离，并维护稳定文件快照。"""

    def __init__(
        self,
        *,
        repository: SandboxWorkspaceRepository,
        provider: SandboxProvider,
        storage: Storage,
        image_ref: str,
        lease_ttl_seconds: int = 600,
        generation_max_seconds: int = 3600,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if lease_ttl_seconds <= 0 or generation_max_seconds < lease_ttl_seconds:
            raise ValueError("Sandbox Lease TTL 配置非法")
        self._repository = repository
        self._provider = provider
        self._storage = storage
        self._image_ref = image_ref
        self._lease_ttl_seconds = lease_ttl_seconds
        self._generation_max_seconds = generation_max_seconds
        self._clock = clock

    async def acquire(self, request: RuntimeTurnRequest) -> SandboxWorkspaceLease:
        """复用健康 generation，或在 DIRTY/过期时轮换并恢复稳定快照。"""
        policy = request.policy_snapshot
        if not policy.sandbox_enabled or policy.network_enabled:
            raise ValueError("当前 Capability Profile 不允许 OpenSandbox Workspace")
        now = self._clock()
        current = await self._repository.get_lease(request.session_id)
        if current is not None:
            self._require_scope(request, current)
        reusable = (
            current is not None
            and current.status is SandboxLeaseStatus.ACTIVE
            and current.image_ref == self._image_ref
            and current.expires_at > now
            and current.generation_started_at
            + timedelta(seconds=self._generation_max_seconds)
            > now
        )
        if (
            reusable
            and current is not None
            and current.holder_turn_run_id != request.turn_run_id
            and not await self._previous_holder_is_stable(current)
        ):
            reusable = False
        if reusable:
            assert current is not None
            backend = await self._provider.connect(current.sandbox_id)
            await self._provider.renew(
                current.sandbox_id, ttl_seconds=self._lease_ttl_seconds
            )
            same_turn = current.holder_turn_run_id == request.turn_run_id
            renewed = replace(
                current,
                holder_turn_run_id=request.turn_run_id,
                fencing_token=(
                    current.fencing_token
                    if same_turn
                    else current.fencing_token + 1
                ),
                expires_at=now + timedelta(seconds=self._lease_ttl_seconds),
                updated_at=now,
            )
            if not await self._repository.replace_lease(
                renewed, expected_fencing_token=current.fencing_token
            ):
                close = getattr(backend, "close", None)
                if callable(close):
                    await asyncio.to_thread(close)
                raise RuntimeError("Sandbox Lease 已被其他执行者认领")
            return SandboxWorkspaceLease(renewed, backend)

        generation = 1 if current is None else current.generation + 1
        backend = await self._provider.create(
            image_ref=self._image_ref,
            ttl_seconds=self._lease_ttl_seconds,
            cpu=1,
            memory_mib=2048,
            network_enabled=False,
            metadata={"session_id": request.session_id, "generation": str(generation)},
        )
        try:
            await self._restore_latest(request.session_id, backend)
            created = SandboxLeaseRecord(
                session_id=request.session_id,
                owner_id=request.context_snapshot.owner_id,
                project_id=request.context_snapshot.project_id,
                holder_turn_run_id=request.turn_run_id,
                sandbox_id=str(backend.id),
                image_ref=self._image_ref,
                generation=generation,
                fencing_token=1 if current is None else current.fencing_token + 1,
                status=SandboxLeaseStatus.ACTIVE,
                generation_started_at=now,
                expires_at=now + timedelta(seconds=self._lease_ttl_seconds),
                updated_at=now,
            )
            expected = None if current is None else current.fencing_token
            if not await self._repository.replace_lease(
                created, expected_fencing_token=expected
            ):
                raise RuntimeError("Sandbox Lease generation 提交冲突")
        except BaseException:
            await self._provider.destroy(str(backend.id))
            raise
        if current is not None:
            await self._best_effort_destroy(current.sandbox_id)
        return SandboxWorkspaceLease(created, backend)

    async def _best_effort_destroy(self, sandbox_id: str) -> None:
        """Lease CAS 已提交后只做回收；失败由 Provider TTL 兜底。"""
        try:
            await self._provider.destroy(sandbox_id)
        except Exception:
            return

    async def _previous_holder_is_stable(self, lease: SandboxLeaseRecord) -> bool:
        """跨 Turn 复用物理环境前，要求上一 holder 已形成当前业务稳定版本。"""
        holder = await self._repository.snapshot_for_turn(lease.holder_turn_run_id)
        latest = await self._repository.latest_snapshot(lease.session_id)
        if holder is None or latest is None:
            return False
        return (
            holder.status is WorkspaceSnapshotStatus.STABLE
            and latest.status is WorkspaceSnapshotStatus.STABLE
            and holder.snapshot_id == latest.snapshot_id
            and holder.owner_id == lease.owner_id
            and holder.project_id == lease.project_id
            and holder.session_id == lease.session_id
            and holder.turn_run_id == lease.holder_turn_run_id
        )

    async def mark_dirty(self, lease: SandboxWorkspaceLease) -> None:
        """只修改内部控制事实；不要求 Provider 仍可连接。"""
        await self._repository.mark_dirty(
            lease.record.session_id,
            lease.record.generation,
            lease.record.fencing_token,
        )

    async def stage_snapshot(
        self, request: RuntimeTurnRequest, lease: SandboxWorkspaceLease
    ) -> WorkspaceSnapshot:
        """取回普通文件并写入不可见 STAGED metadata。"""
        self._require_scope(request, lease.record)
        if lease.record.holder_turn_run_id != request.turn_run_id:
            raise ValueError("Sandbox Lease holder 与 Turn scope 不匹配")
        existing = await self._repository.snapshot_for_turn(request.turn_run_id)
        if existing is not None:
            return existing
        previous = await self._repository.latest_snapshot(request.session_id)
        listed = await asyncio.to_thread(lease.backend.list_workspace_files)
        paths: list[str] = []
        declared_sizes: dict[str, int] = {}
        declared_total = 0
        for path, entry_type, size in listed:
            normalized_type = entry_type.lower().rsplit(".", 1)[-1]
            if normalized_type in {"directory", "dir"}:
                if path != "/workspace" and not is_workspace_file_path(path):
                    raise ValueError("WorkspaceSnapshot 目录路径非法")
                continue
            if normalized_type != "file":
                raise ValueError("WorkspaceSnapshot 只允许普通文件")
            if not is_workspace_file_path(path):
                raise ValueError("WorkspaceSnapshot 路径非法")
            if path in declared_sizes:
                raise ValueError("WorkspaceSnapshot 文件路径不得重复")
            if size < 0 or size > WORKSPACE_MAX_FILE_BYTES:
                raise ValueError("WorkspaceSnapshot 单文件声明大小超过 10 MiB")
            declared_total += size
            if declared_total > WORKSPACE_MAX_TOTAL_BYTES:
                raise ValueError("WorkspaceSnapshot 声明总大小超过 50 MiB")
            declared_sizes[path] = size
            paths.append(path)
        if len(paths) > WORKSPACE_MAX_FILES:
            raise ValueError("WorkspaceSnapshot 文件数超过 128")
        downloaded = await asyncio.to_thread(lease.backend.download_files, paths)
        response_paths = [response.path for response in downloaded]
        if (
            len(response_paths) != len(paths)
            or len(set(response_paths)) != len(response_paths)
            or set(response_paths) != set(paths)
        ):
            raise ValueError("WorkspaceSnapshot 下载响应集合与请求不一致")
        by_path = {response.path: response for response in downloaded}
        contents: list[tuple[str, bytes]] = []
        for path in paths:
            response = by_path[path]
            if response.error is not None or response.content is None:
                raise ValueError("WorkspaceSnapshot 文件取回失败")
            if len(response.content) != declared_sizes[path]:
                raise ValueError("WorkspaceSnapshot Provider 声明大小与实际内容不一致")
            contents.append((path, response.content))
        version = 1 if previous is None else previous.version + 1
        files = tuple(
            WorkspaceFile(
                path=path,
                content_hash=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            for path, content in contents
        )
        snapshot = create_workspace_snapshot(
            owner_id=request.context_snapshot.owner_id,
            project_id=request.context_snapshot.project_id,
            session_id=request.session_id,
            turn_run_id=request.turn_run_id,
            version=version,
            sandbox_generation=lease.record.generation,
            files=files,
        )
        for item, (_, content) in zip(snapshot.files, sorted(contents), strict=True):
            if hashlib.sha256(content).hexdigest() != item.content_hash:
                raise ValueError("WorkspaceSnapshot 内容排序或哈希不一致")
            await self._storage.write(snapshot.storage_key_for(item), content)
        if not await self._repository.stage_snapshot(
            snapshot,
            lease_generation=lease.record.generation,
            fencing_token=lease.record.fencing_token,
        ):
            existing = await self._repository.snapshot_for_turn(request.turn_run_id)
            if existing is not None:
                return existing
            raise RuntimeError("Sandbox Lease 已失权，拒绝暂存 WorkspaceSnapshot")
        return snapshot

    async def _restore_latest(self, session_id: str, backend: Any) -> None:
        snapshot = await self._repository.latest_snapshot(session_id)
        if snapshot is None:
            return
        payload: list[tuple[str, bytes]] = []
        for item in snapshot.files:
            content = await self._storage.read(snapshot.storage_key_for(item))
            if (
                len(content) != item.size_bytes
                or hashlib.sha256(content).hexdigest() != item.content_hash
            ):
                raise ValueError("WorkspaceSnapshot blob 校验失败")
            payload.append((item.path, content))
        responses = await asyncio.to_thread(backend.upload_files, payload)
        response_paths = [item.path for item in responses]
        expected_paths = [path for path, _ in payload]
        if (
            len(response_paths) != len(expected_paths)
            or len(set(response_paths)) != len(response_paths)
            or set(response_paths) != set(expected_paths)
            or any(item.error is not None for item in responses)
        ):
            raise ValueError("WorkspaceSnapshot 恢复失败")

    @staticmethod
    def _require_scope(request: RuntimeTurnRequest, record: SandboxLeaseRecord) -> None:
        if (
            record.session_id != request.session_id
            or record.owner_id != request.context_snapshot.owner_id
            or record.project_id != request.context_snapshot.project_id
        ):
            raise ValueError("Sandbox Lease owner/Project/Session scope 不匹配")
