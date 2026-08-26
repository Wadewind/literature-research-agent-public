"""Agent WorkspaceSnapshot 的纯领域边界。"""

from dataclasses import replace

import pytest

from literature_agent.domain.workspace_snapshot import (
    WorkspaceFile,
    create_workspace_snapshot,
)


def _file(path: str, *, size: int = 3, content_hash: str = "a" * 64) -> WorkspaceFile:
    return WorkspaceFile(path=path, content_hash=content_hash, size_bytes=size)


def test_workspace_snapshot_is_canonical_and_content_addressed() -> None:
    snapshot = create_workspace_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        version=2,
        sandbox_generation=3,
        files=(_file("/workspace/z.md"), _file("/workspace/a.csv", content_hash="b" * 64)),
    )

    assert [item.path for item in snapshot.files] == [
        "/workspace/a.csv",
        "/workspace/z.md",
    ]
    assert snapshot.total_size_bytes == 6
    assert len(snapshot.manifest_hash) == 64
    assert snapshot.storage_key_for(snapshot.files[0]) == (
        "agent-workspaces/owner-1/session-1/blobs/" + "b" * 64
    )


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/out.txt",
        "/workspace/../secret",
        "/workspace",
        "/workspace//double.txt",
        "relative.txt",
    ],
)
def test_workspace_file_rejects_paths_outside_normalized_workspace(path: str) -> None:
    with pytest.raises(ValueError, match="Workspace"):
        _file(path)


def test_workspace_snapshot_enforces_file_and_total_limits() -> None:
    with pytest.raises(ValueError, match="单文件"):
        _file("/workspace/large.bin", size=(10 * 1024 * 1024) + 1)

    files = tuple(
        _file(f"/workspace/{index}.bin", size=400 * 1024, content_hash=f"{index:064x}")
        for index in range(129)
    )
    with pytest.raises(ValueError, match="文件数"):
        create_workspace_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            version=1,
            sandbox_generation=1,
            files=files,
        )

    oversized = tuple(
        _file(f"/workspace/{index}.bin", size=10 * 1024 * 1024, content_hash=f"{index:064x}")
        for index in range(6)
    )
    with pytest.raises(ValueError, match="总大小"):
        create_workspace_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            version=1,
            sandbox_generation=1,
            files=oversized,
        )


def test_workspace_snapshot_rejects_tampered_manifest_on_restore() -> None:
    snapshot = create_workspace_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        version=1,
        sandbox_generation=1,
        files=(_file("/workspace/notes.md"),),
    )

    with pytest.raises(ValueError, match="manifest_hash"):
        replace(snapshot, manifest_hash="0" * 64)
