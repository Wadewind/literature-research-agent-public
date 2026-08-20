"""外部依赖就绪探针 Port。"""

from typing import Protocol


class ReadinessProbe(Protocol):
    """检查单个外部依赖是否可用。"""

    @property
    def name(self) -> str:
        """返回稳定的依赖名称。"""
        ...

    async def check(self) -> None:
        """依赖可用时正常返回，否则抛出异常。"""
        ...
