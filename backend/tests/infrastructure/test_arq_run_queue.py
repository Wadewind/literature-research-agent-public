"""ARQ Run Queue 适配器测试。"""

from unittest.mock import AsyncMock

import pytest
from arq.jobs import JobStatus

from literature_agent.infrastructure.queue import arq_run_queue
from literature_agent.infrastructure.queue.arq_run_queue import (
    ArqRunQueue,
    RunQueueEnqueueError,
)


class _StatusJob:
    """返回预设 ARQ 状态的最小 Job 替身。"""

    statuses: list[JobStatus] = []

    def __init__(self, job_id: str, *, redis) -> None:
        assert job_id.startswith("run:")
        self._redis = redis

    async def status(self) -> JobStatus:
        return self.statuses.pop(0)


def _make_queue(pool: AsyncMock) -> ArqRunQueue:
    """构造使用测试连接池的 Queue。"""
    queue = ArqRunQueue("redis://unused")
    queue._pool = pool  # noqa: SLF001 - 适配器边界测试需要替换惰性连接池
    return queue


async def test_enqueue_returns_when_arq_accepts_new_job() -> None:
    """ARQ 返回 Job 时表示新 Job 已创建。"""
    pool = AsyncMock()
    pool.enqueue_job.return_value = object()

    await _make_queue(pool).enqueue_run("run-1")

    pool.enqueue_job.assert_awaited_once_with(
        "execute_run", "run-1", _job_id="run:run-1"
    )
    pool.delete.assert_not_awaited()


@pytest.mark.parametrize(
    "status", [JobStatus.queued, JobStatus.deferred, JobStatus.in_progress]
)
async def test_duplicate_active_job_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, status: JobStatus
) -> None:
    """相同 ID 已有可执行 Job 时，重复投递是幂等成功。"""
    pool = AsyncMock()
    pool.enqueue_job.return_value = None
    _StatusJob.statuses = [status]
    monkeypatch.setattr(arq_run_queue, "Job", _StatusJob)

    await _make_queue(pool).enqueue_run("run-1")

    pool.enqueue_job.assert_awaited_once()
    pool.delete.assert_not_awaited()


async def test_complete_result_is_deleted_by_exact_key_then_reenqueued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧完成 Result 只按精确 key 清理，并立即重投同一稳定 Job ID。"""
    pool = AsyncMock()
    pool.enqueue_job.side_effect = [None, object()]
    _StatusJob.statuses = [JobStatus.complete]
    monkeypatch.setattr(arq_run_queue, "Job", _StatusJob)

    await _make_queue(pool).enqueue_run("run-1")

    assert pool.enqueue_job.await_count == 2
    pool.delete.assert_awaited_once_with("arq:result:run:run-1")


async def test_not_found_race_is_retried_with_strict_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无法确认 Job 的竞态只能有限重试，最终必须向 Outbox 报错。"""
    pool = AsyncMock()
    pool.enqueue_job.return_value = None
    _StatusJob.statuses = [JobStatus.not_found] * 3
    monkeypatch.setattr(arq_run_queue, "Job", _StatusJob)

    with pytest.raises(RunQueueEnqueueError, match="无法确认 ARQ Job 已被接受"):
        await _make_queue(pool).enqueue_run("run-1")

    assert pool.enqueue_job.await_count == 3
    pool.delete.assert_not_awaited()
