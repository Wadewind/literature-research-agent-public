"""PostgreSQL 与 Valkey 就绪探针适配器。"""

from arq.connections import RedisSettings, create_pool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseReadinessProbe:
    """通过只读查询检查 PostgreSQL 连接。"""

    name = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        """执行最小查询；连接或查询失败时由调用方统一降级。"""
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


class ValkeyReadinessProbe:
    """通过 PING 检查 Valkey 连接。"""

    name = "valkey"

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def check(self) -> None:
        """创建短连接执行 PING，并保证连接池被关闭。"""
        pool = await create_pool(RedisSettings.from_dsn(self._redis_url))
        try:
            await pool.ping()
        finally:
            await pool.aclose()
