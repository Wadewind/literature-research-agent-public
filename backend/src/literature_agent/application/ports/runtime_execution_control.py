"""Runtime Adapter 使用的 SDK-neutral Execution 控制 Port。"""

from typing import Protocol

from literature_agent.domain.runtime_execution import (
    RuntimeExecution,
    RuntimeExecutionPermit,
)


class RuntimeExecutionControl(Protocol):
    """短事务 lease/fencing 操作；实现不得在事务中调用模型、Tool 或 Checkpoint。"""

    async def claim(
        self,
        *,
        turn_run_id: str,
        session_id: str,
        runtime_execution_id: str,
        request_hash: str,
        owner_id: str,
    ) -> RuntimeExecution: ...

    async def get(self, turn_run_id: str) -> RuntimeExecution | None: ...

    async def can_recover(self, turn_run_id: str) -> bool: ...

    async def assert_active(self, permit: RuntimeExecutionPermit) -> RuntimeExecution: ...

    async def renew(self, permit: RuntimeExecutionPermit) -> RuntimeExecution: ...

    async def record_checkpoint(
        self, permit: RuntimeExecutionPermit, checkpoint_id: str
    ) -> RuntimeExecution: ...

    async def succeed(
        self, permit: RuntimeExecutionPermit, checkpoint_id: str
    ) -> RuntimeExecution: ...

    async def temporary_error(
        self, permit: RuntimeExecutionPermit, *, code: str, safe_message: str
    ) -> RuntimeExecution: ...

    async def fail(
        self, permit: RuntimeExecutionPermit, *, code: str, safe_message: str
    ) -> RuntimeExecution: ...

    async def cancel_for_business(self, turn_run_id: str) -> RuntimeExecution | None: ...
