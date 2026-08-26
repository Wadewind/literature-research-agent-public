"""Runtime Execution Repository 内存假实现。"""

from literature_agent.application.ports.runtime_execution_repository import (
    RuntimeExecutionRepository,
)
from literature_agent.domain.runtime_execution import RuntimeExecution


class FakeRuntimeExecutionRepository(RuntimeExecutionRepository):
    """以字典模拟唯一 Turn 和行锁后的存取。"""

    def __init__(self) -> None:
        self._items: dict[str, RuntimeExecution] = {}

    async def get(self, turn_run_id: str) -> RuntimeExecution | None:
        return self._items.get(turn_run_id)

    async def get_for_update(self, turn_run_id: str) -> RuntimeExecution | None:
        return self._items.get(turn_run_id)

    async def add_if_absent(self, execution: RuntimeExecution) -> bool:
        if execution.turn_run_id in self._items:
            return False
        self._items[execution.turn_run_id] = execution
        return True

    async def save(
        self, execution: RuntimeExecution, *, expected: RuntimeExecution
    ) -> bool:
        if self._items.get(execution.turn_run_id) != expected:
            return False
        self._items[execution.turn_run_id] = execution
        return True
