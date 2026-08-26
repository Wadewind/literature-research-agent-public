"""Checkpoint Adapter 的安全与连接契约测试。"""

from typing import cast

import pytest

from literature_agent.infrastructure.workflow.postgres_checkpoint import (
    PostgresCheckpointPool,
    _psycopg_url,
    strict_checkpoint_serializer,
)


def test_sqlalchemy_url_is_converted_without_hiding_password() -> None:
    converted = _psycopg_url("postgresql+psycopg://user:secret@db:5432/app")
    assert converted == "postgresql://user:secret@db:5432/app"


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        _psycopg_url("sqlite+aiosqlite:///tmp/app.db")


def test_serializer_rejects_unlisted_custom_type() -> None:
    class UnsafeType:
        pass

    serializer = strict_checkpoint_serializer()
    with pytest.raises(TypeError):
        serializer.dumps_typed(UnsafeType())


async def test_pool_uses_fixed_bounds_and_returns_one_saver_per_execution(monkeypatch) -> None:
    class _Pool:
        def __init__(self, connection_string, **kwargs) -> None:
            self.connection_string = connection_string
            self.kwargs = kwargs
            self.opened = False
            self.closed = False
            created.append(self)

        async def open(self, *, wait: bool) -> None:
            assert wait is True
            self.opened = True

        async def close(self) -> None:
            self.closed = True

    created: list[_Pool] = []

    class _Saver:
        def __init__(self, pool, *, serde) -> None:
            self.pool = pool
            self.serde = serde

    monkeypatch.setattr(
        "literature_agent.infrastructure.workflow.postgres_checkpoint.AsyncConnectionPool",
        _Pool,
    )
    monkeypatch.setattr(
        "literature_agent.infrastructure.workflow.postgres_checkpoint.AsyncPostgresSaver",
        _Saver,
    )
    pool = PostgresCheckpointPool(
        "postgresql+psycopg://user:secret@db:5432/app", min_size=1, max_size=4
    )

    async with pool.open() as factory:
        first = cast(_Saver, factory.create_saver())
        second = cast(_Saver, factory.create_saver())

    assert first is not second
    assert first.pool is second.pool is created[0]
    assert created[0].kwargs["min_size"] == 1
    assert created[0].kwargs["max_size"] == 4
    assert created[0].opened is True
    assert created[0].closed is True
