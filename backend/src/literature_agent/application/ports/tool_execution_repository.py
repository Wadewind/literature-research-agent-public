"""Project Tool effect 持久化端口。"""

from typing import Protocol

from literature_agent.domain.tool_execution import ToolExecution, ToolExecutionStatus


class ToolExecutionRepository(Protocol):
    async def add(self, value: ToolExecution) -> ToolExecution: ...
    async def get(self, effect_id: str) -> ToolExecution | None: ...
    async def list_by_turn(self, turn_run_id: str) -> list[ToolExecution]: ...
    async def count_by_turn(self, turn_run_id: str) -> int: ...
    async def save(
        self,
        value: ToolExecution,
        *,
        expected_status: ToolExecutionStatus,
        expected_attempt_count: int,
    ) -> bool: ...
