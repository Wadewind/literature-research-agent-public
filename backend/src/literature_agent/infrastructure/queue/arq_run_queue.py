"""基于 ARQ/Valkey 的 RunQueue 适配器。"""

from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.constants import result_key_prefix
from arq.jobs import Job, JobStatus

from literature_agent.application.ports.run_queue import RunQueue

# ARQ 中执行 Run 的 Job 函数名，与 Worker 注册保持一致
EXECUTE_RUN_FUNCTION = "execute_run"
_MAX_ENQUEUE_ATTEMPTS = 3
_ACTIVE_JOB_STATUSES = {
    JobStatus.queued,
    JobStatus.deferred,
    JobStatus.in_progress,
}


class RunQueueEnqueueError(RuntimeError):
    """ARQ 未能确认稳定 ID 对应的 Job 已被接受。"""


class ArqRunQueue(RunQueue):
    """把 ``run_id`` 投递到 ARQ 的队列适配器。

    Job 只携带稳定 ``run_id``，不携带 PDF、Prompt 或全文；
    使用 ``run:<run_id>`` 作为 ARQ Job ID，队列内同一时间同一 Run
    只会有一个待执行 Job，重复投递天然去重。
    """

    def __init__(self, redis_url: str) -> None:
        """初始化适配器。

        连接池在首次投递时惰性创建，避免 API 启动时强依赖 Valkey 可用。

        参数:
            redis_url: Valkey/Redis 连接串。
        """
        self._redis_url = redis_url
        self._pool: ArqRedis | None = None

    async def enqueue_run(self, run_id: str) -> None:
        """投递一个只携带 ``run_id`` 的 ARQ Job。

        ARQ 以 ``None`` 同时表达活跃 Job 去重、旧 Result 阻塞和并发竞态。
        这里必须进一步确认状态，避免 Outbox 把未实际入队的 Run 标记为
        已投递。旧 Result 不是业务事实，只精确清理当前稳定 Job ID 的 key。
        """
        pool = await self._get_pool()
        job_id = f"run:{run_id}"

        for _attempt in range(_MAX_ENQUEUE_ATTEMPTS):
            accepted = await pool.enqueue_job(
                EXECUTE_RUN_FUNCTION,
                run_id,
                _job_id=job_id,
            )
            if accepted is not None:
                return

            status = await Job(job_id, redis=pool).status()
            if status in _ACTIVE_JOB_STATUSES:
                return
            if status is JobStatus.complete:
                await pool.delete(result_key_prefix + job_id)
                continue
            if status is JobStatus.not_found:
                # enqueue_job 的 WATCH 竞态可能返回 None，但随后已找不到 key；
                # 下一次循环重新尝试，次数有严格上界。
                continue

            raise RunQueueEnqueueError(
                f"ARQ Job 状态不可识别，无法确认投递: job_id={job_id}"
            )

        raise RunQueueEnqueueError(
            f"无法确认 ARQ Job 已被接受: job_id={job_id}, "
            f"attempts={_MAX_ENQUEUE_ATTEMPTS}"
        )

    async def aclose(self) -> None:
        """关闭连接池。"""
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None

    async def _get_pool(self) -> ArqRedis:
        """惰性创建并返回 ARQ 连接池。"""
        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(self._redis_url))
        return self._pool
