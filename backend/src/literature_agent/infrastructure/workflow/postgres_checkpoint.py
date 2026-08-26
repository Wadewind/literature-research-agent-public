"""LangGraph PostgreSQL checkpoint 连接与安全序列化适配器。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url


def _psycopg_url(database_url: str) -> str:
    """把 SQLAlchemy psycopg URL 转为 psycopg 可接受的连接字符串。"""
    url = make_url(database_url)
    if url.drivername not in {"postgresql+psycopg", "postgresql"}:
        raise ValueError("Checkpoint 只支持 PostgreSQL psycopg URL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def strict_checkpoint_serializer() -> JsonPlusSerializer:
    """只允许内建安全类型，禁止 pickle 和任意模块反序列化。"""
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=(),
    )


class PostgresCheckpointStore:
    """按显式生命周期提供 AsyncPostgresSaver，不在运行时修改 schema。"""

    def __init__(self, database_url: str) -> None:
        self._connection_string = _psycopg_url(database_url)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[AsyncPostgresSaver]:
        """建立符合官方要求的 autocommit + dict_row 连接并确保关闭。"""
        connection = cast(
            AsyncConnection[DictRow],
            await AsyncConnection.connect(
                self._connection_string,
                autocommit=True,
                row_factory=cast(Any, dict_row),
            ),
        )
        try:
            yield AsyncPostgresSaver(
                connection,
                serde=strict_checkpoint_serializer(),
            )
        finally:
            await connection.close()


@dataclass(frozen=True, slots=True)
class PostgresCheckpointExecutionFactory:
    """共享连接池，但为每次 Runtime 操作构造独立 Saver 实例。"""

    pool: Any

    def create_saver(self) -> AsyncPostgresSaver:
        """避免 singleton Saver 的实例锁串行全部 Execution I/O。"""
        return AsyncPostgresSaver(
            self.pool,
            serde=strict_checkpoint_serializer(),
        )


class PostgresCheckpointPool:
    """Worker 生命周期内的有界 PostgreSQL Checkpoint 连接池。"""

    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 4) -> None:
        if min_size < 0 or max_size < 1 or min_size > max_size:
            raise ValueError("Checkpoint pool size 配置非法")
        self._connection_string = _psycopg_url(database_url)
        self._min_size = min_size
        self._max_size = max_size

    @asynccontextmanager
    async def open(self) -> AsyncIterator[PostgresCheckpointExecutionFactory]:
        """显式打开/关闭 pool；普通测试可替换为完全离线实现。"""
        pool = AsyncConnectionPool(
            self._connection_string,
            min_size=self._min_size,
            max_size=self._max_size,
            open=False,
            kwargs={
                "autocommit": True,
                "row_factory": cast(Any, dict_row),
            },
        )
        await pool.open(wait=True)
        try:
            yield PostgresCheckpointExecutionFactory(pool)
        finally:
            await pool.close()
