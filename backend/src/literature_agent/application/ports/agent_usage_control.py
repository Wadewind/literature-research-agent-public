"""Runtime 使用的 SDK-neutral Agent 硬预算端口。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from literature_agent.domain.agent_usage import AgentToolCall, AgentTurnUsage


@dataclass(frozen=True, slots=True)
class ToolCallReservationRequest:
    """Tool 调用预留；携带稳定身份、哈希与有界脱敏输入预览。"""

    invocation_id: str
    tool_name: str
    input_schema_hash: str
    args_hash: str
    input_size_bytes: int
    input_preview: str | None = None
    input_preview_truncated: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeBudget:
    """Runtime 每个模型/Tool 边界都要重新读取的预算投影。"""

    deadline_at: datetime
    tool_timeout_seconds: int
    execute_timeout_seconds: int
    max_tool_output_bytes: int
    max_input_tokens_per_model_call: int
    max_output_tokens_per_model_call: int


class AgentUsageControl(Protocol):
    async def start_turn(self, turn_run_id: str) -> RuntimeBudget: ...
    async def reserve_model_call(
        self, turn_run_id: str, ordinal: int, *, approximate_input_tokens: int
    ) -> AgentTurnUsage: ...
    async def record_model_usage(
        self,
        turn_run_id: str,
        ordinal: int,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None: ...
    async def reserve_tool_call(
        self, turn_run_id: str, request: ToolCallReservationRequest
    ) -> AgentToolCall: ...
    async def start_tool_call(self, turn_run_id: str, reservation_key: str) -> AgentToolCall: ...
    async def succeed_tool_call(
        self,
        turn_run_id: str,
        reservation_key: str,
        *,
        output_size_bytes: int,
        result_hash: str,
        output_preview: str | None = None,
        output_preview_truncated: bool = False,
    ) -> AgentToolCall: ...
    async def fail_tool_call(
        self,
        turn_run_id: str,
        reservation_key: str,
        *,
        error_code: str,
        safe_message: str,
        output_preview: str | None = None,
        output_preview_truncated: bool = False,
    ) -> AgentToolCall: ...
