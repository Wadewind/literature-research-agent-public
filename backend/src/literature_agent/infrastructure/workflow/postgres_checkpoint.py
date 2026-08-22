"""LangGraph PostgreSQL checkpoint 连接与安全序列化适配器。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
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
