"""Agent Turn 的持久化硬预算与脱敏 Tool 调用事实。"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

AGENT_TURN_WALL_CLOCK_SECONDS = 300
AGENT_TOOL_TIMEOUT_SECONDS = 60
AGENT_EXECUTE_TIMEOUT_SECONDS = 60
AGENT_MAX_TOOL_OUTPUT_BYTES = 64 * 1024
AGENT_MAX_REPEATED_TOOL_CALLS = 2
AGENT_MAX_INPUT_TOKENS_PER_MODEL_CALL = 60_000
AGENT_MAX_OUTPUT_TOKENS_PER_MODEL_CALL = 2_048

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AgentToolCallStatus(StrEnum):
    """公开 Tool 调用摘要的生命周期。"""

    RESERVED = "reserved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentModelCallReservation:
    """模型调用的稳定 reservation；不保存 Prompt 或响应正文。"""

    reservation_key: str
    turn_run_id: str
    ordinal: int
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(
            reservation_key=self.reservation_key,
            turn_run_id=self.turn_run_id,
        )
        if len(self.reservation_key) > 255 or self.ordinal < 1:
            raise ValueError("模型调用 reservation 身份非法")
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("input_tokens 不能小于 0")
        if self.output_tokens is not None and self.output_tokens < 0:
            raise ValueError("output_tokens 不能小于 0")

    def record_tokens(
        self,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        now: datetime | None = None,
    ) -> AgentModelCallReservation:
        """Provider 不提供数据时保持 NULL；同一 reservation 只能同值重放。"""
        if input_tokens is not None and input_tokens < 0:
            raise ValueError("input_tokens 不能小于 0")
        if output_tokens is not None and output_tokens < 0:
            raise ValueError("output_tokens 不能小于 0")
        if (
            self.input_tokens is not None
            and input_tokens is not None
            and self.input_tokens != input_tokens
        ):
            raise ValueError("模型 input Token Usage 重放发生冲突")
        if (
            self.output_tokens is not None
            and output_tokens is not None
            and self.output_tokens != output_tokens
        ):
            raise ValueError("模型 output Token Usage 重放发生冲突")
        merged_input = self.input_tokens if self.input_tokens is not None else input_tokens
        merged_output = self.output_tokens if self.output_tokens is not None else output_tokens
        if (merged_input, merged_output) == (self.input_tokens, self.output_tokens):
            return self
        value = now or datetime.now(UTC)
        return replace(
            self,
            input_tokens=merged_input,
            output_tokens=merged_output,
            updated_at=value,
        )


@dataclass(frozen=True, slots=True)
class AgentTurnUsage:
    """一个 Turn 的版本化硬预算及累计用量。"""

    turn_run_id: str
    owner_id: str
    project_id: str
    session_id: str
    policy_snapshot_id: str
    max_model_calls: int
    max_tool_calls: int
    wall_clock_limit_seconds: int
    tool_timeout_seconds: int
    execute_timeout_seconds: int
    max_tool_output_bytes: int
    max_repeated_tool_calls: int
    max_input_tokens_per_model_call: int
    max_output_tokens_per_model_call: int
    model_calls_reserved: int
    tool_calls_reserved: int
    input_tokens: int | None
    output_tokens: int | None
    started_at: datetime | None
    deadline_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(
            turn_run_id=self.turn_run_id,
            owner_id=self.owner_id,
            project_id=self.project_id,
            session_id=self.session_id,
            policy_snapshot_id=self.policy_snapshot_id,
        )
        if self.max_model_calls < 0 or self.max_tool_calls < 0:
            raise ValueError("Agent 调用预算不能小于 0")
        if (
            self.wall_clock_limit_seconds <= 0
            or self.tool_timeout_seconds <= 0
            or self.execute_timeout_seconds <= 0
            or self.max_tool_output_bytes <= 0
            or self.max_repeated_tool_calls <= 0
            or self.max_input_tokens_per_model_call <= 0
            or self.max_output_tokens_per_model_call <= 0
        ):
            raise ValueError("Agent 硬预算上限必须为正数")
        if not 0 <= self.model_calls_reserved <= self.max_model_calls:
            raise ValueError("模型调用用量超出预算")
        if not 0 <= self.tool_calls_reserved <= self.max_tool_calls:
            raise ValueError("Tool 调用量超出预算")
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("input_tokens 不能小于 0")
        if self.output_tokens is not None and self.output_tokens < 0:
            raise ValueError("output_tokens 不能小于 0")
        if (self.started_at is None) != (self.deadline_at is None):
            raise ValueError("Agent Turn 起始时间与 deadline 必须同时存在")
        if self.started_at is not None:
            assert self.deadline_at is not None
            if self.deadline_at <= self.started_at:
                raise ValueError("Agent Turn deadline 必须晚于起始时间")

    def start(self, *, now: datetime | None = None) -> AgentTurnUsage:
        """首次 Runtime 边界冻结 deadline；重试不会重置。"""
        if self.started_at is not None:
            return self
        value = now or datetime.now(UTC)
        return replace(
            self,
            started_at=value,
            deadline_at=value + timedelta(seconds=self.wall_clock_limit_seconds),
            updated_at=value,
        )


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    """不含 raw args/result/endpoint 的 Tool invocation 业务摘要。"""

    reservation_key: str
    turn_run_id: str
    invocation_id: str
    tool_name: str
    tool_version: str
    input_schema_hash: str
    args_hash: str
    status: AgentToolCallStatus
    input_size_bytes: int
    output_size_bytes: int | None
    result_hash: str | None
    error_code: str | None
    safe_message: str | None
    duration_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(
            reservation_key=self.reservation_key,
            turn_run_id=self.turn_run_id,
            invocation_id=self.invocation_id,
            tool_name=self.tool_name,
            tool_version=self.tool_version,
        )
        if len(self.reservation_key) > 255 or len(self.invocation_id) > 255:
            raise ValueError("Tool invocation 身份过长")
        if len(self.tool_name) > 100 or len(self.tool_version) > 100:
            raise ValueError("Tool 名称或版本过长")
        if not _SHA256_PATTERN.fullmatch(self.input_schema_hash):
            raise ValueError("Tool input_schema_hash 必须是 SHA-256")
        if not _SHA256_PATTERN.fullmatch(self.args_hash):
            raise ValueError("Tool args_hash 必须是 SHA-256")
        if self.input_size_bytes < 0:
            raise ValueError("Tool 输入大小不能小于 0")
        if self.output_size_bytes is not None and not (
            0 <= self.output_size_bytes <= AGENT_MAX_TOOL_OUTPUT_BYTES
        ):
            raise ValueError("Tool 输出大小超出安全上限")
        if self.result_hash is not None and not _SHA256_PATTERN.fullmatch(self.result_hash):
            raise ValueError("Tool result_hash 必须是 SHA-256")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("Tool duration_ms 不能小于 0")
        if self.error_code is not None and (
            not self.error_code.strip() or len(self.error_code) > 100
        ):
            raise ValueError("Tool 安全错误码非法")
        if self.safe_message is not None and (
            not self.safe_message.strip() or len(self.safe_message) > 500
        ):
            raise ValueError("Tool 安全错误说明非法")
        self._validate_state()

    def _validate_state(self) -> None:
        if self.status is AgentToolCallStatus.RESERVED:
            if any(
                value is not None
                for value in (
                    self.started_at,
                    self.completed_at,
                    self.output_size_bytes,
                    self.result_hash,
                    self.error_code,
                    self.safe_message,
                    self.duration_ms,
                )
            ):
                raise ValueError("RESERVED Tool 调用不能包含执行结果")
        elif self.status is AgentToolCallStatus.RUNNING:
            if self.started_at is None or any(
                value is not None
                for value in (
                    self.completed_at,
                    self.output_size_bytes,
                    self.result_hash,
                    self.error_code,
                    self.safe_message,
                    self.duration_ms,
                )
            ):
                raise ValueError("RUNNING Tool 调用状态非法")
        elif self.status is AgentToolCallStatus.SUCCEEDED:
            if (
                self.started_at is None
                or self.completed_at is None
                or self.output_size_bytes is None
                or self.result_hash is None
                or self.duration_ms is None
                or self.error_code is not None
                or self.safe_message is not None
            ):
                raise ValueError("SUCCEEDED Tool 调用状态非法")
        elif (
            self.started_at is None
            or self.completed_at is None
            or self.duration_ms is None
            or self.error_code is None
            or self.safe_message is None
            or self.output_size_bytes is not None
            or self.result_hash is not None
        ):
            raise ValueError("FAILED Tool 调用状态非法")

    def start(self, *, now: datetime | None = None) -> AgentToolCall:
        if self.status is AgentToolCallStatus.RUNNING:
            return self
        if self.status is not AgentToolCallStatus.RESERVED:
            raise ValueError("只有 RESERVED Tool 调用可以开始")
        value = now or datetime.now(UTC)
        return replace(
            self,
            status=AgentToolCallStatus.RUNNING,
            started_at=value,
            updated_at=value,
        )

    def succeed(
        self,
        *,
        output_size_bytes: int,
        result_hash: str,
        now: datetime | None = None,
    ) -> AgentToolCall:
        if self.status is not AgentToolCallStatus.RUNNING or self.started_at is None:
            raise ValueError("只有 RUNNING Tool 调用可以成功")
        value = now or datetime.now(UTC)
        return replace(
            self,
            status=AgentToolCallStatus.SUCCEEDED,
            output_size_bytes=output_size_bytes,
            result_hash=result_hash,
            duration_ms=_duration_ms(self.started_at, value),
            completed_at=value,
            updated_at=value,
        )

    def fail(
        self,
        *,
        error_code: str,
        safe_message: str,
        now: datetime | None = None,
    ) -> AgentToolCall:
        if self.status is not AgentToolCallStatus.RUNNING or self.started_at is None:
            raise ValueError("只有 RUNNING Tool 调用可以失败")
        if not error_code.strip() or not safe_message.strip():
            raise ValueError("Tool 安全错误信息非法")
        value = now or datetime.now(UTC)
        return replace(
            self,
            status=AgentToolCallStatus.FAILED,
            error_code=error_code,
            safe_message=safe_message,
            duration_ms=_duration_ms(self.started_at, value),
            completed_at=value,
            updated_at=value,
        )


def create_agent_turn_usage(
    *,
    turn_run_id: str,
    owner_id: str,
    project_id: str,
    session_id: str,
    policy_snapshot_id: str,
    max_model_calls: int,
    max_tool_calls: int,
    wall_clock_limit_seconds: int = AGENT_TURN_WALL_CLOCK_SECONDS,
    tool_timeout_seconds: int = AGENT_TOOL_TIMEOUT_SECONDS,
    execute_timeout_seconds: int = AGENT_EXECUTE_TIMEOUT_SECONDS,
    max_tool_output_bytes: int = AGENT_MAX_TOOL_OUTPUT_BYTES,
    max_repeated_tool_calls: int = AGENT_MAX_REPEATED_TOOL_CALLS,
    max_input_tokens_per_model_call: int = AGENT_MAX_INPUT_TOKENS_PER_MODEL_CALL,
    max_output_tokens_per_model_call: int = AGENT_MAX_OUTPUT_TOKENS_PER_MODEL_CALL,
    now: datetime | None = None,
) -> AgentTurnUsage:
    """按精简交付 Profile 创建尚未起表的预算事实。"""
    value = now or datetime.now(UTC)
    return AgentTurnUsage(
        turn_run_id=turn_run_id,
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        policy_snapshot_id=policy_snapshot_id,
        max_model_calls=max_model_calls,
        max_tool_calls=max_tool_calls,
        wall_clock_limit_seconds=wall_clock_limit_seconds,
        tool_timeout_seconds=tool_timeout_seconds,
        execute_timeout_seconds=execute_timeout_seconds,
        max_tool_output_bytes=max_tool_output_bytes,
        max_repeated_tool_calls=max_repeated_tool_calls,
        max_input_tokens_per_model_call=max_input_tokens_per_model_call,
        max_output_tokens_per_model_call=max_output_tokens_per_model_call,
        model_calls_reserved=0,
        tool_calls_reserved=0,
        input_tokens=None,
        output_tokens=None,
        started_at=None,
        deadline_at=None,
        created_at=value,
        updated_at=value,
    )


def create_agent_model_call_reservation(
    *,
    turn_run_id: str,
    ordinal: int,
    now: datetime | None = None,
) -> AgentModelCallReservation:
    value = now or datetime.now(UTC)
    return AgentModelCallReservation(
        reservation_key=f"model:{turn_run_id}:{ordinal}",
        turn_run_id=turn_run_id,
        ordinal=ordinal,
        input_tokens=None,
        output_tokens=None,
        created_at=value,
        updated_at=value,
    )


def create_agent_tool_call(
    *,
    turn_run_id: str,
    invocation_id: str,
    tool_name: str,
    tool_version: str,
    input_schema_hash: str,
    args_hash: str,
    input_size_bytes: int,
    now: datetime | None = None,
) -> AgentToolCall:
    """创建稳定 Tool reservation；原始参数不进入领域事实。"""
    value = now or datetime.now(UTC)
    return AgentToolCall(
        reservation_key=f"tool:{turn_run_id}:{invocation_id}",
        turn_run_id=turn_run_id,
        invocation_id=invocation_id,
        tool_name=tool_name,
        tool_version=tool_version,
        input_schema_hash=input_schema_hash,
        args_hash=args_hash,
        status=AgentToolCallStatus.RESERVED,
        input_size_bytes=input_size_bytes,
        output_size_bytes=None,
        result_hash=None,
        error_code=None,
        safe_message=None,
        duration_ms=None,
        started_at=None,
        completed_at=None,
        created_at=value,
        updated_at=value,
    )


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    if completed_at < started_at:
        raise ValueError("Tool 完成时间不能早于开始时间")
    return int((completed_at - started_at).total_seconds() * 1_000)


def _require_non_empty(**values: str) -> None:
    for name, value in values.items():
        if not value.strip():
            raise ValueError(f"{name} 不能为空")
