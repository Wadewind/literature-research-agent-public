"""Agent 逻辑 Workspace 的受控文件快照。"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

WORKSPACE_ROOT = "/workspace"
WORKSPACE_MAX_FILES = 128
WORKSPACE_MAX_FILE_BYTES = 10 * 1024 * 1024
WORKSPACE_MAX_TOTAL_BYTES = 50 * 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorkspaceSnapshotStatus(StrEnum):
    """只有 STABLE 快照可以成为恢复来源。"""

    STAGED = "staged"
    STABLE = "stable"


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    """快照 Manifest 中一个普通文件的内容寻址引用。"""

    path: str
    content_hash: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not is_workspace_file_path(self.path):
            raise ValueError("Workspace 文件必须位于规范化的 /workspace 子路径")
        if not _SHA256.fullmatch(self.content_hash):
            raise ValueError("Workspace 文件 content_hash 必须是小写 SHA-256")
        if not 0 <= self.size_bytes <= WORKSPACE_MAX_FILE_BYTES:
            raise ValueError("Workspace 单文件大小超过 10 MiB")


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """成功 Turn 推进的、可从 Storage 重建的内部工作文件清单。"""

    snapshot_id: str
    schema_version: str
    owner_id: str
    project_id: str
    session_id: str
    turn_run_id: str
    version: int
    sandbox_generation: int
    files: tuple[WorkspaceFile, ...]
    total_size_bytes: int
    manifest_hash: str
    created_at: datetime
    status: WorkspaceSnapshotStatus = WorkspaceSnapshotStatus.STAGED

    def __post_init__(self) -> None:
        if self.schema_version != "agent-workspace-snapshot.v1":
            raise ValueError("WorkspaceSnapshot schema_version 不受支持")
        if not all(
            value.strip()
            for value in (
                self.snapshot_id,
                self.owner_id,
                self.project_id,
                self.session_id,
                self.turn_run_id,
            )
        ):
            raise ValueError("WorkspaceSnapshot scope 不能为空")
        if self.version < 1 or self.sandbox_generation < 1:
            raise ValueError("WorkspaceSnapshot version/generation 必须为正整数")
        if len(self.files) > WORKSPACE_MAX_FILES:
            raise ValueError("WorkspaceSnapshot 文件数超过 128")
        if self.files != tuple(sorted(self.files, key=lambda item: item.path)):
            raise ValueError("WorkspaceSnapshot files 必须按路径规范排序")
        if len({item.path for item in self.files}) != len(self.files):
            raise ValueError("WorkspaceSnapshot 文件路径不得重复")
        total = sum(item.size_bytes for item in self.files)
        if total != self.total_size_bytes or total > WORKSPACE_MAX_TOTAL_BYTES:
            raise ValueError("WorkspaceSnapshot 总大小或 Manifest 不一致")
        if self.manifest_hash != _manifest_hash(
            owner_id=self.owner_id,
            project_id=self.project_id,
            session_id=self.session_id,
            turn_run_id=self.turn_run_id,
            version=self.version,
            sandbox_generation=self.sandbox_generation,
            files=self.files,
            total_size_bytes=self.total_size_bytes,
        ):
            raise ValueError("WorkspaceSnapshot manifest_hash 校验失败")

    def as_stable(self) -> WorkspaceSnapshot:
        """表示已由 Runtime 与业务 Run 成功事务共同授权的稳定快照。"""
        return replace(self, status=WorkspaceSnapshotStatus.STABLE)

    def storage_key_for(self, item: WorkspaceFile) -> str:
        """返回 owner/Session 隔离的内容寻址 blob key。"""
        return (
            f"agent-workspaces/{self.owner_id}/{self.session_id}/blobs/"
            f"{item.content_hash}"
        )


def is_workspace_file_path(path: str) -> bool:
    """只接受 `/workspace` 下无歧义、无穿越的 POSIX 文件路径。"""
    if not path.startswith(f"{WORKSPACE_ROOT}/") or "//" in path:
        return False
    if path.endswith("/") or "\x00" in path:
        return False
    normalized = posixpath.normpath(path)
    return normalized == path and normalized != WORKSPACE_ROOT


def create_workspace_snapshot(
    *,
    owner_id: str,
    project_id: str,
    session_id: str,
    turn_run_id: str,
    version: int,
    sandbox_generation: int,
    files: tuple[WorkspaceFile, ...],
) -> WorkspaceSnapshot:
    """校验有界 Manifest，并生成稳定哈希。"""
    if not all(value.strip() for value in (owner_id, project_id, session_id, turn_run_id)):
        raise ValueError("WorkspaceSnapshot scope 不能为空")
    if version < 1 or sandbox_generation < 1:
        raise ValueError("WorkspaceSnapshot version/generation 必须为正整数")
    if len(files) > WORKSPACE_MAX_FILES:
        raise ValueError("WorkspaceSnapshot 文件数超过 128")
    ordered = tuple(sorted(files, key=lambda item: item.path))
    paths = [item.path for item in ordered]
    if len(paths) != len(set(paths)):
        raise ValueError("WorkspaceSnapshot 文件路径不得重复")
    total = sum(item.size_bytes for item in ordered)
    if total > WORKSPACE_MAX_TOTAL_BYTES:
        raise ValueError("WorkspaceSnapshot 总大小超过 50 MiB")
    manifest_hash = _manifest_hash(
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        version=version,
        sandbox_generation=sandbox_generation,
        files=ordered,
        total_size_bytes=total,
    )
    return WorkspaceSnapshot(
        snapshot_id=str(uuid4()),
        schema_version="agent-workspace-snapshot.v1",
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        version=version,
        sandbox_generation=sandbox_generation,
        files=ordered,
        total_size_bytes=total,
        manifest_hash=manifest_hash,
        created_at=datetime.now(UTC),
    )


def _manifest_hash(
    *,
    owner_id: str,
    project_id: str,
    session_id: str,
    turn_run_id: str,
    version: int,
    sandbox_generation: int,
    files: tuple[WorkspaceFile, ...],
    total_size_bytes: int,
) -> str:
    payload = {
        "schema_version": "agent-workspace-snapshot.v1",
        "owner_id": owner_id,
        "project_id": project_id,
        "session_id": session_id,
        "turn_run_id": turn_run_id,
        "version": version,
        "sandbox_generation": sandbox_generation,
        "files": [
            {
                "path": item.path,
                "content_hash": item.content_hash,
                "size_bytes": item.size_bytes,
            }
            for item in files
        ],
        "total_size_bytes": total_size_bytes,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
