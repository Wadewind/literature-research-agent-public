"""声明来源公网目标解析 Port。"""

from typing import Protocol


class PublicSourceResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...
