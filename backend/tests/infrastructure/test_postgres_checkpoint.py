"""Checkpoint Adapter 的安全与连接契约测试。"""

import pytest

from literature_agent.infrastructure.workflow.postgres_checkpoint import (
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
