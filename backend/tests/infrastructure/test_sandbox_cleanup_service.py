"""Sandbox 清理补偿的离线、确定性行为。"""

from datetime import UTC, datetime, timedelta

from literature_agent.infrastructure.agent.sandbox_cleanup import SandboxCleanupService
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxCleanupStatus,
    create_sandbox_cleanup_task,
)


class _Repository:
    def __init__(self) -> None:
        self.tasks = []
        self.completed: list[tuple[str, str, int]] = []
        self.retried: list[tuple[str, str, int, str, str]] = []
        self.scheduled = 0

    async def enqueue_due_cleanups(self, *, now, limit):
        del now, limit
        return self.scheduled

    async def claim_cleanup_tasks(
        self, *, worker_id, now, lease_seconds, limit
    ):
        del now, lease_seconds
        claimed = []
        for task in self.tasks[:limit]:
            if task.status is SandboxCleanupStatus.PENDING:
                task = task.as_running(worker_id=worker_id)
                claimed.append(task)
        self.tasks = claimed + self.tasks[len(claimed) :]
        return tuple(claimed)

    async def complete_cleanup(
        self, cleanup_id, *, worker_id, attempt_count, now
    ):
        del now
        self.completed.append((cleanup_id, worker_id, attempt_count))
        return True

    async def retry_cleanup(
        self,
        cleanup_id,
        *,
        worker_id,
        attempt_count,
        next_attempt_at,
        error_code,
        error_summary,
        now,
    ):
        del next_attempt_at, now
        self.retried.append(
            (cleanup_id, worker_id, attempt_count, error_code, error_summary)
        )
        return True


class _Provider:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.destroyed: list[str] = []

    async def destroy(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError(f"provider response lost for {sandbox_id}")


def _task(now: datetime):
    return create_sandbox_cleanup_task(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        sandbox_id="private-sandbox-id",
        generation=3,
        fencing_token=7,
        reason="expired",
        now=now,
    )


async def test_cleanup_runs_provider_io_between_short_repository_calls() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    repository = _Repository()
    repository.tasks = [_task(now)]
    provider = _Provider()
    service = SandboxCleanupService(
        repository=repository,
        provider=provider,
        worker_id="worker-1",
        clock=lambda: now,
    )

    result = await service.run_once()

    assert result.scheduled == 0
    assert result.claimed == 1
    assert result.succeeded == 1
    assert result.retried == 0
    assert provider.destroyed == ["private-sandbox-id"]
    assert repository.completed == [
        (repository.tasks[0].cleanup_id, "worker-1", 1)
    ]


async def test_response_loss_is_saved_as_bounded_safe_retry_without_identifier() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    repository = _Repository()
    repository.tasks = [_task(now)]
    provider = _Provider(fail_once=True)
    service = SandboxCleanupService(
        repository=repository,
        provider=provider,
        worker_id="worker-1",
        clock=lambda: now,
    )

    result = await service.run_once()

    assert result.retried == 1
    assert repository.completed == []
    [retry] = repository.retried
    assert retry[3:] == (
        "provider_destroy_failed",
        "远端 Sandbox 销毁暂时失败",
    )
    assert "private-sandbox-id" not in " ".join(map(str, retry))

    # 模拟短租约到期后的下一轮认领；Provider 的 destroy 必须可安全重试。
    repository.tasks = [
        repository.tasks[0].as_pending(
            next_attempt_at=now + timedelta(seconds=2),
            error_code=retry[3],
            error_summary=retry[4],
        )
    ]
    service = SandboxCleanupService(
        repository=repository,
        provider=provider,
        worker_id="worker-2",
        clock=lambda: now + timedelta(seconds=2),
    )
    second = await service.run_once()

    assert second.succeeded == 1
    assert provider.destroyed == ["private-sandbox-id", "private-sandbox-id"]
