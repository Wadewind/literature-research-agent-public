"""事务会话抽象端口。"""

from typing import Protocol


class Session(Protocol):
    """应用层使用的最小事务会话抽象。

    SQLAlchemy ``AsyncSession`` 与 Fake 实现均通过结构类型匹配该端口，
    应用服务不依赖具体 ORM。
    """

    async def flush(self) -> None:
        """将当前变更刷新到持久化层（不提交事务）。"""
        ...

    async def commit(self) -> None:
        """提交当前事务。"""
        ...
