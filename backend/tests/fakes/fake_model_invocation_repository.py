"""Model Invocation Repository 的内存假实现。"""

from literature_agent.application.ports.model_invocation_repository import (
    ModelInvocationRepository,
)
from literature_agent.domain.model_invocation import ModelInvocation


class FakeModelInvocationRepository(ModelInvocationRepository):
    """不依赖数据库的 Model Invocation Repository 假实现。"""

    def __init__(self, fail_on_add: bool = False) -> None:
        self._invocations: dict[str, ModelInvocation] = {}
        self._fail_on_add = fail_on_add

    async def add(self, invocation: ModelInvocation) -> ModelInvocation:
        """将调用记录存入内存；``fail_on_add`` 时模拟持久化失败。"""
        if self._fail_on_add:
            raise RuntimeError("模拟持久化失败")
        self._invocations[invocation.invocation_id] = invocation
        return invocation

    async def list_by_run(self, run_id: str) -> list[ModelInvocation]:
        """按 Run 返回调用记录，按创建时间升序。"""
        records = [i for i in self._invocations.values() if i.run_id == run_id]
        records.sort(key=lambda i: i.created_at)
        return records

    # 测试辅助：直接访问内部状态
    def all(self) -> list[ModelInvocation]:
        """返回全部记录（测试断言用）。"""
        return list(self._invocations.values())
