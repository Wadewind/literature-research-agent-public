"""Research Agent Runtime 的应用层端口与项目自有 DTO。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from literature_agent.domain.research_agent import (
    ContextSnapshot,
    PolicySnapshot,
    RuntimeSessionBinding,
    RuntimeTurnBinding,
)


class RuntimeExecutionState(StrEnum):
    """Runtime 侧一次 Turn Execution 的归一化状态。"""

    RUNNING = "running"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeEventKind(StrEnum):
    """Adapter 可以向业务执行层暴露的白名单增量类型。"""

    BOUND = "bound"
    STARTED = "started"
    ASSISTANT_DELTA = "assistant_delta"
    INTERRUPTED = "interrupted"
    RESUMED = "resumed"
    COMPLETED = "completed"


class RuntimeErrorKind(StrEnum):
    """供业务重试策略使用的最小 Runtime 错误分类。"""

    TEMPORARY = "temporary"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"


class ResearchAgentRuntimeError(Exception):
    """已归一化且只携带安全描述的 Runtime 错误。"""

    def __init__(self, *, kind: RuntimeErrorKind, code: str, safe_message: str) -> None:
        self.kind = kind
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class RuntimeTurnRequest:
    """开始一轮执行所需的小型业务输入。"""

    session_id: str
    turn_run_id: str
    user_message_id: str
    user_message_content: str
    context_snapshot: ContextSnapshot
    policy_snapshot: PolicySnapshot

    def __post_init__(self) -> None:
        context = self.context_snapshot
        policy = self.policy_snapshot
        if not self.user_message_content.strip():
            raise ValueError("Runtime 用户消息不能为空")
        if (
            context.session_id != self.session_id
            or context.turn_run_id != self.turn_run_id
            or context.user_message_id != self.user_message_id
        ):
            raise ValueError("ContextSnapshot 与 RuntimeTurnRequest 作用域不一致")
        if policy.session_id != self.session_id or policy.turn_run_id != self.turn_run_id:
            raise ValueError("PolicySnapshot 与 RuntimeTurnRequest 作用域不一致")
        if context.owner_id != policy.owner_id or context.project_id != policy.project_id:
            raise ValueError("ContextSnapshot 与 PolicySnapshot 所有权不一致")


@dataclass(frozen=True, slots=True)
class RuntimeResumeRequest:
    """从相同 Execution/Checkpoint 恢复一轮的受控输入。"""

    turn_run_id: str
    response: str | None

    def __post_init__(self) -> None:
        if not self.turn_run_id.strip():
            raise ValueError("turn_run_id 不能为空")
        if self.response is not None and len(self.response) > 16_000:
            raise ValueError("Runtime 恢复输入长度不能超过 16000")


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Runtime 产生的短暂、可筛选增量；不是持久业务 Event。"""

    event_id: str
    turn_run_id: str
    sequence: int
    kind: RuntimeEventKind
    text_delta: str | None = None
    safe_summary: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeArtifactCandidate:
    """待平台 staged/校验的候选 Artifact 描述，不携带文件正文。"""

    candidate_id: str
    name: str
    media_type: str
    content_ref: str
    content_hash: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class RuntimeTurnResult:
    """Runtime 成功结果；业务层仍需独立提交 Message/Evidence/Artifact。"""

    turn_run_id: str
    assistant_content: str
    evidence_ids: tuple[str, ...] = ()
    artifact_candidates: tuple[RuntimeArtifactCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeTurnReconciliation:
    """用于崩溃/响应丢失后对账的 Runtime 状态快照。"""

    turn_run_id: str
    state: RuntimeExecutionState
    session_binding: RuntimeSessionBinding
    turn_binding: RuntimeTurnBinding
    last_event_sequence: int
    result_available: bool


class ResearchAgentRuntime(Protocol):
    """Deep Agents 等内部 Runtime 的最小业务边界。"""

    def execute_turn(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        """幂等启动一轮，或重放同一逻辑 Execution 的已知增量。"""
        ...

    def resume_turn(self, request: RuntimeResumeRequest) -> AsyncIterator[RuntimeEvent]:
        """从同一 Execution/Checkpoint 恢复，不创建第二个 Execution。"""
        ...

    async def cancel_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        """请求停止后续模型/Tool 操作，并返回当前 Runtime 状态。"""
        ...

    async def reconcile_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        """读取 Runtime 状态，不把它直接视为业务 Run 状态。"""
        ...

    async def collect_turn_result(self, turn_run_id: str) -> RuntimeTurnResult:
        """重复收集同一成功结果，供业务层 Effectively Once 提交。"""
        ...
