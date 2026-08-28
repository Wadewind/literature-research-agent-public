"""Sandbox 过期/脏环境的事务外销毁补偿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxCleanupTask,
)


class SandboxCleanupRepository(Protocol):
    """每个方法自行完成一个短事务。"""

    async def enqueue_due_cleanups(self, *, now: datetime, limit: int) -> int: ...
    async def claim_cleanup_tasks(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[SandboxCleanupTask, ...]: ...
    async def complete_cleanup(
        self,
        cleanup_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        now: datetime,
    ) -> bool: ...
    async def retry_cleanup(
        self,
        cleanup_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        next_attempt_at: datetime,
        error_code: str,
        error_summary: str,
        now: datetime,
    ) -> bool: ...


class SandboxDestroyProvider(Protocol):
    async def destroy(self, sandbox_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SandboxCleanupResult:
    scheduled: int
    claimed: int
    succeeded: int
    retried: int


class SandboxCleanupService:
    """先认领内部事实，事务外 destroy，再以旧认领 fence 提交结果。"""

    def __init__(
        self,
        *,
        repository: SandboxCleanupRepository,
        provider: SandboxDestroyProvider,
        worker_id: str,
        batch_size: int = 20,
        claim_lease_seconds: int = 30,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        if not worker_id.strip() or batch_size <= 0 or claim_lease_seconds <= 0:
            raise ValueError("Sandbox cleanup worker/budget 配置非法")
        self._repository = repository
        self._provider = provider
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._claim_lease_seconds = claim_lease_seconds
        self._clock = clock

    async def run_once(self) -> SandboxCleanupResult:
        now = self._clock()
        scheduled = await self._repository.enqueue_due_cleanups(
            now=now,
            limit=self._batch_size,
        )
        claimed = await self._repository.claim_cleanup_tasks(
            worker_id=self._worker_id,
            now=now,
            lease_seconds=self._claim_lease_seconds,
            limit=self._batch_size,
        )
        succeeded = 0
        retried = 0
        for task in claimed:
            try:
                await self._provider.destroy(task.sandbox_id)
            except Exception:
                retry_at = self._clock() + timedelta(
                    seconds=min(2 ** min(task.attempt_count, 8), 300)
                )
                changed = await self._repository.retry_cleanup(
                    task.cleanup_id,
                    worker_id=self._worker_id,
                    attempt_count=task.attempt_count,
                    next_attempt_at=retry_at,
                    error_code="provider_destroy_failed",
                    error_summary="远端 Sandbox 销毁暂时失败",
                    now=self._clock(),
                )
                retried += int(changed)
            else:
                changed = await self._repository.complete_cleanup(
                    task.cleanup_id,
                    worker_id=self._worker_id,
                    attempt_count=task.attempt_count,
                    now=self._clock(),
                )
                succeeded += int(changed)
        return SandboxCleanupResult(
            scheduled=scheduled,
            claimed=len(claimed),
            succeeded=succeeded,
            retried=retried,
        )
