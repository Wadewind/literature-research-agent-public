"""平台自有 Project Tool effect 的幂等业务事实。"""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

TOOL_RESULT_MAX_CHARS = 8_000
TOOL_ARGUMENTS_MAX_CHARS = 4_000


class ToolExecutionStatus(StrEnum):
    """Tool effect 的持久状态。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolErrorKind(StrEnum):
    """失败是否允许相同 effect 重试。"""

    TEMPORARY = "temporary"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """不保存原始参数，只保存 canonical hash 与有界安全结果。"""

    effect_id: str
    turn_run_id: str
    tool_name: str
    args_hash: str
    status: ToolExecutionStatus
    result_payload: dict | None
    result_hash: str | None
    error_kind: ToolErrorKind | None
    error_code: str | None
    safe_message: str | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime

    def succeed(self, result_payload: dict) -> "ToolExecution":
        if self.status is not ToolExecutionStatus.RUNNING:
            raise ValueError("只有 RUNNING Tool effect 可以成功")
        if not isinstance(result_payload, dict):
            raise ValueError("Tool 结果必须是 JSON 对象")
        try:
            serialized = canonical_tool_args(result_payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("Tool 结果必须是有限 JSON") from exc
        if len(serialized) > TOOL_RESULT_MAX_CHARS:
            raise ValueError("Tool 结果超过安全大小限制")
        safe_copy = json.loads(serialized)
        return replace(
            self,
            status=ToolExecutionStatus.SUCCEEDED,
            result_payload=safe_copy,
            result_hash=hashlib.sha256(serialized.encode()).hexdigest(),
            error_kind=None,
            error_code=None,
            safe_message=None,
            updated_at=datetime.now(UTC),
        )

    def fail(
        self, kind: ToolErrorKind, code: str, safe_message: str
    ) -> "ToolExecution":
        if self.status is not ToolExecutionStatus.RUNNING:
            raise ValueError("只有 RUNNING Tool effect 可以失败")
        if (
            not code
            or len(code) > 100
            or not safe_message
            or len(safe_message) > 500
        ):
            raise ValueError("Tool 安全错误信息非法")
        return replace(
            self,
            status=ToolExecutionStatus.FAILED,
            result_payload=None,
            result_hash=None,
            error_kind=kind,
            error_code=code,
            safe_message=safe_message,
            updated_at=datetime.now(UTC),
        )

    def retry(self) -> "ToolExecution":
        if (
            self.status is not ToolExecutionStatus.FAILED
            or self.error_kind is not ToolErrorKind.TEMPORARY
        ):
            raise ValueError("只有 temporary 失败的 Tool effect 可以重试")
        return replace(
            self,
            status=ToolExecutionStatus.RUNNING,
            error_kind=None,
            error_code=None,
            safe_message=None,
            attempt_count=self.attempt_count + 1,
            updated_at=datetime.now(UTC),
        )


def canonical_tool_args(arguments: Any) -> str:
    """生成稳定、无空白的 canonical JSON。"""
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def create_tool_execution(
    *,
    turn_run_id: str,
    tool_name: str,
    arguments: dict,
    invocation_id: str | None = None,
) -> ToolExecution:
    """为 Project 参数或 MCP 逻辑 invocation 创建稳定 effect。"""
    if not turn_run_id or not tool_name or len(tool_name) > 100:
        raise ValueError("Tool effect 作用域或名称非法")
    if not isinstance(arguments, dict):
        raise ValueError("Tool 参数必须是 JSON 对象")
    try:
        serialized = canonical_tool_args(arguments)
    except (TypeError, ValueError) as exc:
        raise ValueError("Tool 参数必须是有限 JSON") from exc
    if len(serialized) > TOOL_ARGUMENTS_MAX_CHARS:
        raise ValueError("Tool 参数超过安全大小限制")
    if invocation_id is not None:
        if (
            not isinstance(invocation_id, str)
            or not invocation_id.strip()
            or len(invocation_id) > 255
        ):
            raise ValueError("Tool invocation ID 非法")
        args_material = canonical_tool_args(
            {"invocation_id": invocation_id, "arguments": arguments}
        )
        args_hash = hashlib.sha256(args_material.encode()).hexdigest()
        effect_hash = hashlib.sha256(
            f"{turn_run_id}:invocation:{invocation_id}".encode()
        ).hexdigest()
    else:
        args_hash = hashlib.sha256(serialized.encode()).hexdigest()
        effect_hash = hashlib.sha256(
            f"{turn_run_id}:{tool_name}:{args_hash}".encode()
        ).hexdigest()
    now = datetime.now(UTC)
    return ToolExecution(
        effect_id=f"tool-effect-{effect_hash}",
        turn_run_id=turn_run_id,
        tool_name=tool_name,
        args_hash=args_hash,
        status=ToolExecutionStatus.RUNNING,
        result_payload=None,
        result_hash=None,
        error_kind=None,
        error_code=None,
        safe_message=None,
        attempt_count=1,
        created_at=now,
        updated_at=now,
    )
