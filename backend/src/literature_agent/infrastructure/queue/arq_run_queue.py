"""基于 ARQ/Valkey 的 RunQueue 适配器。"""

from arq.connections import ArqRedis, RedisSettings, create_pool

from literature_agent.application.ports.run_queue import RunQueue

# ARQ 中执行 Run 的 Job 函数名，与 Worker 注册保持一致
EXECUTE_RUN_FUNCTION = "execute_run"


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
        """投递一个只携带 ``run_id`` 的 ARQ Job。"""
        pool = await self._get_pool()
        await pool.enqueue_job(EXECUTE_RUN_FUNCTION, run_id, _job_id=f"run:{run_id}")

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
