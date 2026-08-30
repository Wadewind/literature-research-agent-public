"""SQLAlchemy 数据库连接配置测试。"""

from typing import Any

from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.persistence import database


def test_create_engine_uses_database_echo_instead_of_debug(monkeypatch) -> None:
    """thinking 所需 debug 不得隐式打开 API/Worker 的 SQL echo。"""
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_create_async_engine(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)

    engine = database.create_engine(Settings(debug=True, database_echo=False))

    assert engine is sentinel
    assert captured == {
        "url": Settings().database_url,
        "echo": False,
        "future": True,
    }
