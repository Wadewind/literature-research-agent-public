"""Sandbox Lease 与 WorkspaceSnapshot 协调器的离线行为。"""

import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.application.ports.research_agent_runtime import RuntimeTurnRequest
from literature_agent.domain.workspace_snapshot import WorkspaceSnapshotStatus
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseRecord,
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
    SandboxWorkspaceManager,
)
from tests.infrastructure.test_deep_agents_research_agent_runtime import _request


class _Repo:
    def __init__(self) -> None:
        self.lease: SandboxLeaseRecord | None = None
        self.snapshots = []

    async def get_lease(self, session_id: str):
        return self.lease if self.lease and self.lease.session_id == session_id else None

    async def replace_lease(self, value, *, expected_fencing_token):
        current = self.lease
        if current is not None and current.fencing_token != expected_fencing_token:
            return False
        if current is None and expected_fencing_token is not None:
            return False
        self.lease = value
        return True

    async def mark_dirty(self, session_id: str, generation: int, fencing_token: int):
        if (
            self.lease is not None
            and self.lease.session_id == session_id
            and self.lease.generation == generation
            and self.lease.fencing_token == fencing_token
        ):
            self.lease = self.lease.as_dirty()
            return True
        return False

    async def latest_snapshot(self, session_id: str):
        values = [
            item
            for item in self.snapshots
            if item.session_id == session_id
            and item.status is WorkspaceSnapshotStatus.STABLE
        ]
        return values[-1] if values else None

    async def snapshot_for_turn(self, turn_run_id: str):
        return next(
            (item for item in self.snapshots if item.turn_run_id == turn_run_id), None
        )

    async def stage_snapshot(self, value, *, lease_generation: int, fencing_token: int):
        assert self.lease is not None
        if (
            self.lease.generation != lease_generation
            or self.lease.fencing_token != fencing_token
        ):
            return False
        self.snapshots.append(value)
        return True


class _Storage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def write(self, key: str, content: bytes) -> None:
        self.data[key] = content

    async def read(self, key: str) -> bytes:
        return self.data[key]


class _Backend:
    def __init__(self, sandbox_id: str) -> None:
        self.id = sandbox_id
        self.files: dict[str, bytes] = {}

    def list_workspace_files(self):
        return [(path, "file", len(content)) for path, content in sorted(self.files.items())]

    def upload_files(self, files):
        from deepagents.backends.protocol import FileUploadResponse

        for path, content in files:
            self.files[path] = content
        return [FileUploadResponse(path=path) for path, _ in files]

    def download_files(self, paths):
        from deepagents.backends.protocol import FileDownloadResponse

        return [FileDownloadResponse(path=path, content=self.files[path]) for path in paths]


class _Provider:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.renewed: list[tuple[str, int]] = []
        self.destroyed: list[str] = []
        self.backends: dict[str, _Backend] = {}

    async def create(self, **kwargs):
        self.created.append(kwargs)
        sandbox_id = f"sandbox-{len(self.created)}"
        backend = _Backend(sandbox_id)
        self.backends[sandbox_id] = backend
        return backend

    async def connect(self, sandbox_id: str):
        return self.backends[sandbox_id]

    async def renew(self, sandbox_id: str, *, ttl_seconds: int):
        self.renewed.append((sandbox_id, ttl_seconds))

    async def destroy(self, sandbox_id: str):
        self.destroyed.append(sandbox_id)


def _manager(repo: _Repo, provider: _Provider, storage: _Storage, clock: list[datetime]):
    return SandboxWorkspaceManager(
        repository=repo,
        provider=provider,
        storage=storage,
        image_ref="research-agent@sha256:pinned",
        clock=lambda: clock[0],
    )


def _sandbox_request(*, turn_run_id: str = "turn-1") -> RuntimeTurnRequest:
    request = _request(turn_run_id=turn_run_id, allowed_tool_names=("execute",))
    return replace(
        request,
        policy_snapshot=replace(
            request.policy_snapshot,
            sandbox_enabled=True,
            approval_required=False,
        ),
    )


async def test_acquire_reuses_one_session_lease_and_renews_sliding_ttl() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    clock = [datetime(2026, 8, 26, tzinfo=UTC)]
    manager = _manager(repo, provider, storage, clock)
    request = _sandbox_request()

    first = await manager.acquire(request)
    clock[0] += timedelta(minutes=2)
    second = await manager.acquire(request)

    assert first.record.session_id == request.session_id
    assert second.record.sandbox_id == first.record.sandbox_id
    assert second.record.generation == 1
    assert second.record.fencing_token == first.record.fencing_token
    assert second.record.holder_turn_run_id == request.turn_run_id
    assert len(provider.created) == 1
    assert provider.created[0]["network_enabled"] is False
    assert provider.created[0]["cpu"] == 1
    assert provider.created[0]["memory_mib"] == 2048
    assert provider.renewed == [(first.record.sandbox_id, 600)]


async def test_new_turn_reuses_generation_but_advances_fence_and_holder() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    clock = [datetime(2026, 8, 26, tzinfo=UTC)]
    manager = _manager(repo, provider, storage, clock)
    first_request = _sandbox_request(turn_run_id="turn-1")
    first = await manager.acquire(first_request)
    staged = await manager.stage_snapshot(first_request, first)
    repo.snapshots[0] = staged.as_stable()

    second = await manager.acquire(_sandbox_request(turn_run_id="turn-2"))

    assert second.record.generation == first.record.generation
    assert second.record.fencing_token == first.record.fencing_token + 1
    assert second.record.holder_turn_run_id == "turn-2"


async def test_new_turn_rotates_when_previous_holder_has_no_snapshot() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    first = await manager.acquire(_sandbox_request(turn_run_id="turn-1"))
    first.backend.files["/workspace/uncommitted.txt"] = b"uncommitted"

    second = await manager.acquire(_sandbox_request(turn_run_id="turn-2"))

    assert second.record.generation == first.record.generation + 1
    assert "/workspace/uncommitted.txt" not in second.backend.files
    assert provider.destroyed == [first.record.sandbox_id]


async def test_new_turn_rotates_from_staged_holder_and_restores_previous_stable() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])

    stable_request = _sandbox_request(turn_run_id="turn-stable")
    stable_lease = await manager.acquire(stable_request)
    stable_lease.backend.files["/workspace/notes.md"] = b"stable"
    stable = await manager.stage_snapshot(stable_request, stable_lease)
    repo.snapshots[0] = stable.as_stable()

    staged_request = _sandbox_request(turn_run_id="turn-staged")
    staged_lease = await manager.acquire(staged_request)
    staged_lease.backend.files["/workspace/notes.md"] = b"uncommitted"
    staged_lease.backend.files["/workspace/new.txt"] = b"new"
    staged = await manager.stage_snapshot(staged_request, staged_lease)
    assert staged.status is WorkspaceSnapshotStatus.STAGED

    next_lease = await manager.acquire(_sandbox_request(turn_run_id="turn-next"))

    assert next_lease.record.generation == staged_lease.record.generation + 1
    assert next_lease.backend.files == {"/workspace/notes.md": b"stable"}
    assert provider.destroyed == [staged_lease.record.sandbox_id]


async def test_initial_lease_cas_loser_destroys_only_its_created_sandbox() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    ready = asyncio.Event()
    create_count = 0
    original_create = provider.create

    async def concurrent_create(**kwargs):
        nonlocal create_count
        backend = await original_create(**kwargs)
        create_count += 1
        if create_count == 2:
            ready.set()
        await ready.wait()
        return backend

    provider.create = concurrent_create  # type: ignore[method-assign]
    clock = [datetime(2026, 8, 26, tzinfo=UTC)]
    managers = [_manager(repo, provider, storage, clock) for _ in range(2)]

    results = await asyncio.gather(
        *(manager.acquire(_sandbox_request()) for manager in managers),
        return_exceptions=True,
    )

    winners = [item for item in results if not isinstance(item, BaseException)]
    losers = [item for item in results if isinstance(item, BaseException)]
    assert len(winners) == 1
    assert len(losers) == 1
    winner = winners[0]
    assert isinstance(winner, SandboxWorkspaceLease)
    assert repo.lease is not None
    assert repo.lease.sandbox_id == winner.record.sandbox_id
    assert provider.destroyed == [
        item.id for item in provider.backends.values() if item.id != repo.lease.sandbox_id
    ]


async def test_dirty_lease_rotates_generation_and_restores_last_stable_snapshot() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    clock = [datetime(2026, 8, 26, tzinfo=UTC)]
    manager = _manager(repo, provider, storage, clock)
    request = _sandbox_request()
    first = await manager.acquire(request)
    first.backend.files["/workspace/notes.md"] = b"stable"
    staged = await manager.stage_snapshot(request, first)
    snapshot = staged.as_stable()
    repo.snapshots[0] = snapshot
    await manager.mark_dirty(first)

    second = await manager.acquire(request)

    assert snapshot.version == 1
    assert second.record.generation == 2
    assert second.backend.files["/workspace/notes.md"] == b"stable"
    assert provider.destroyed == [first.record.sandbox_id]


async def test_rotation_cas_loser_destroys_only_new_sandbox_not_renewed_current() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    first = await manager.acquire(_sandbox_request(turn_run_id="turn-1"))
    await manager.mark_dirty(first)
    original_replace = repo.replace_lease

    async def lose_after_concurrent_renewal(value, *, expected_fencing_token):
        assert repo.lease is not None
        repo.lease = replace(
            repo.lease,
            status=SandboxLeaseStatus.ACTIVE,
            fencing_token=repo.lease.fencing_token + 1,
        )
        return False

    repo.replace_lease = lose_after_concurrent_renewal  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="generation 提交冲突"):
        await manager.acquire(_sandbox_request(turn_run_id="turn-2"))

    repo.replace_lease = original_replace  # type: ignore[method-assign]
    assert provider.destroyed == ["sandbox-2"]
    assert first.record.sandbox_id not in provider.destroyed
    assert repo.lease is not None
    assert repo.lease.sandbox_id == first.record.sandbox_id


async def test_rotation_old_sandbox_destroy_failure_does_not_undo_new_lease() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    first = await manager.acquire(_sandbox_request(turn_run_id="turn-1"))
    await manager.mark_dirty(first)
    original_destroy = provider.destroy

    async def fail_old_destroy(sandbox_id: str) -> None:
        if sandbox_id == first.record.sandbox_id:
            raise RuntimeError("provider unavailable")
        await original_destroy(sandbox_id)

    provider.destroy = fail_old_destroy  # type: ignore[method-assign]

    second = await manager.acquire(_sandbox_request(turn_run_id="turn-2"))

    assert second.record.generation == 2
    assert repo.lease == second.record
    assert second.record.sandbox_id != first.record.sandbox_id


async def test_snapshot_rejects_symlink_and_does_not_advance_stable_version() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    clock = [datetime(2026, 8, 26, tzinfo=UTC)]
    manager = _manager(repo, provider, storage, clock)
    request = _sandbox_request()
    lease = await manager.acquire(request)
    lease.backend.list_workspace_files = lambda: [("/workspace/link", "symlink", 1)]

    with pytest.raises(ValueError, match="普通文件"):
        await manager.stage_snapshot(request, lease)

    assert repo.snapshots == []
    assert storage.data == {}


async def test_repeated_completed_turn_reuses_same_stable_snapshot() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    clock = [datetime(2026, 8, 26, tzinfo=UTC)]
    manager = _manager(repo, provider, storage, clock)
    request = _sandbox_request()
    lease = await manager.acquire(request)
    lease.backend.files["/workspace/notes.md"] = b"stable"

    staged = await manager.stage_snapshot(request, lease)
    first = staged.as_stable()
    repo.snapshots[0] = first
    lease.backend.files["/workspace/notes.md"] = b"uncommitted-replay-change"
    replayed = await manager.stage_snapshot(request, lease)

    assert replayed == first
    assert len(repo.snapshots) == 1
    assert storage.data[first.storage_key_for(first.files[0])] == b"stable"


async def test_staged_snapshot_is_invisible_until_promoted() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    request = _sandbox_request()
    lease = await manager.acquire(request)
    lease.backend.files["/workspace/notes.md"] = b"staged"

    staged = await manager.stage_snapshot(request, lease)

    assert staged.status is WorkspaceSnapshotStatus.STAGED
    assert await repo.latest_snapshot(request.session_id) is None


async def test_snapshot_unique_race_returns_existing_same_turn_snapshot() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    request = _sandbox_request()
    lease = await manager.acquire(request)
    lease.backend.files["/workspace/notes.md"] = b"stable"

    async def losing_insert(value, *, lease_generation, fencing_token):
        del lease_generation, fencing_token
        repo.snapshots.append(value)
        return False

    repo.stage_snapshot = losing_insert  # type: ignore[method-assign]
    snapshot = await manager.stage_snapshot(request, lease)

    assert snapshot.turn_run_id == request.turn_run_id
    assert len(repo.snapshots) == 1


async def test_snapshot_skips_directories_and_offloads_sync_remote_io() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    clock = [datetime(2026, 8, 26, tzinfo=UTC)]
    manager = _manager(repo, provider, storage, clock)
    request = _sandbox_request()
    lease = await manager.acquire(request)
    main_thread = threading.get_ident()
    calls: list[tuple[str, int]] = []
    lease.backend.files["/workspace/nested/plot.png"] = b"png"

    def listed():
        calls.append(("list", threading.get_ident()))
        return [
            ("/workspace/nested", "directory", 0),
            ("/workspace/nested/plot.png", "file", 3),
        ]

    original_download = lease.backend.download_files

    def downloaded(paths):
        calls.append(("download", threading.get_ident()))
        return original_download(paths)

    lease.backend.list_workspace_files = listed
    lease.backend.download_files = downloaded

    snapshot = await manager.stage_snapshot(request, lease)

    assert [item.path for item in snapshot.files] == ["/workspace/nested/plot.png"]
    assert all(thread_id != main_thread for _, thread_id in calls)


async def test_snapshot_rejects_oversize_metadata_before_download() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    lease = await manager.acquire(_sandbox_request())
    download_calls = 0
    lease.backend.list_workspace_files = lambda: [
        ("/workspace/large.bin", "file", (10 * 1024 * 1024) + 1)
    ]

    def downloaded(paths):
        nonlocal download_calls
        del paths
        download_calls += 1
        return []

    lease.backend.download_files = downloaded

    with pytest.raises(ValueError, match="单文件"):
        await manager.stage_snapshot(_sandbox_request(), lease)
    assert download_calls == 0


@pytest.mark.parametrize("mode", ["missing", "extra", "duplicate", "mismatched"])
async def test_snapshot_requires_exact_download_response_set(mode: str) -> None:
    from deepagents.backends.protocol import FileDownloadResponse

    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    request = _sandbox_request()
    lease = await manager.acquire(request)
    lease.backend.list_workspace_files = lambda: [
        ("/workspace/a.txt", "file", 1),
        ("/workspace/b.txt", "file", 1),
    ]
    responses = {
        "missing": [FileDownloadResponse(path="/workspace/a.txt", content=b"a")],
        "extra": [
            FileDownloadResponse(path="/workspace/a.txt", content=b"a"),
            FileDownloadResponse(path="/workspace/b.txt", content=b"b"),
            FileDownloadResponse(path="/workspace/c.txt", content=b"c"),
        ],
        "duplicate": [
            FileDownloadResponse(path="/workspace/a.txt", content=b"a"),
            FileDownloadResponse(path="/workspace/a.txt", content=b"a"),
        ],
        "mismatched": [
            FileDownloadResponse(path="/workspace/a.txt", content=b"a"),
            FileDownloadResponse(path="/workspace/c.txt", content=b"c"),
        ],
    }
    lease.backend.download_files = lambda paths: responses[mode]

    with pytest.raises(ValueError, match="响应集合"):
        await manager.stage_snapshot(request, lease)


async def test_snapshot_rejects_provider_size_mismatch() -> None:
    repo, provider, storage = _Repo(), _Provider(), _Storage()
    manager = _manager(repo, provider, storage, [datetime(2026, 8, 26, tzinfo=UTC)])
    request = _sandbox_request()
    lease = await manager.acquire(request)
    lease.backend.files["/workspace/a.txt"] = b"actual"
    lease.backend.list_workspace_files = lambda: [("/workspace/a.txt", "file", 1)]

    with pytest.raises(ValueError, match="声明大小"):
        await manager.stage_snapshot(request, lease)
