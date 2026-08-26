"""Runtime Execution 持久控制 Repository Port。"""

from typing import Protocol

from literature_agent.domain.runtime_execution import RuntimeExecution


class RuntimeExecutionRepository(Protocol):
    """使用行锁和唯一约束收敛一个 Turn 的唯一 Execution。"""

    async def get(self, turn_run_id: str) -> RuntimeExecution | None: ...

    async def get_for_update(self, turn_run_id: str) -> RuntimeExecution | None: ...

    async def add_if_absent(self, execution: RuntimeExecution) -> bool: ...

    async def save(
        self, execution: RuntimeExecution, *, expected: RuntimeExecution
    ) -> bool: ...
