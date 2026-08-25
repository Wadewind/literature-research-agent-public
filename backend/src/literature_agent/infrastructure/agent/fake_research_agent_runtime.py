"""完全离线、确定性的 ResearchAgentRuntime Fake。"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeArtifactCandidate,
    RuntimeErrorKind,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeExecutionState,
    RuntimeResumeRequest,
    RuntimeTurnReconciliation,
    RuntimeTurnRequest,
    RuntimeTurnResult,
)
from literature_agent.domain.research_agent import (
    RuntimeSessionBinding,
    RuntimeTurnBinding,
)


@dataclass(slots=True)
class _TurnRecord:
    request: RuntimeTurnRequest
    session_binding: RuntimeSessionBinding
    turn_binding: RuntimeTurnBinding
    execute_events: tuple[RuntimeEvent, ...]
    state: RuntimeExecutionState
    result_plan: RuntimeTurnResult
    result: RuntimeTurnResult | None = None
    execute_consumed: bool = False
    resume_request: RuntimeResumeRequest | None = None
    resume_events: tuple[RuntimeEvent, ...] = ()
    last_event_sequence: int = 0


class FakeResearchAgentRuntime:
    """不访问模型、网络或付费能力的 Runtime 契约实现。"""

    def __init__(self, *, interrupt_turn_ids: frozenset[str] = frozenset()) -> None:
        self._interrupt_turn_ids = interrupt_turn_ids
        self._session_bindings: dict[str, RuntimeSessionBinding] = {}
        self._turn_records: dict[str, _TurnRecord] = {}
        self._execution_start_count = 0

    @property
    def execution_start_count(self) -> int:
        """本 Fake 实际建立过的逻辑 Execution 数，供契约测试断言。"""
        return self._execution_start_count

    @property
    def session_binding_count(self) -> int:
        """当前建立的 Session Binding 数。"""
        return len(self._session_bindings)

    @property
    def turn_binding_count(self) -> int:
        """当前建立的 Turn Binding 数。"""
        return len(self._turn_records)

    def execute_turn(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        """首次执行创建稳定映射；重复执行重放相同增量。"""
        return self._execute_stream(request)

    async def _execute_stream(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        record = self._get_or_create_record(request)
        if record.request != request:
            raise _runtime_error(
                RuntimeErrorKind.PERMANENT,
                "runtime_turn_conflict",
                "同一 turn_run_id 已绑定不同输入",
            )
        if record.state is RuntimeExecutionState.CANCELLED:
            return
        if record.execute_consumed:
            for event in record.execute_events:
                yield event
            return

        for event in record.execute_events:
            if record.state is RuntimeExecutionState.CANCELLED:
                return
            self._apply_event(record, event)
            record.last_event_sequence = event.sequence
            yield event
        record.execute_consumed = True

    def resume_turn(self, request: RuntimeResumeRequest) -> AsyncIterator[RuntimeEvent]:
        """只允许 interrupted Turn 使用相同响应幂等恢复。"""
        return self._resume_stream(request)

    async def _resume_stream(self, request: RuntimeResumeRequest) -> AsyncIterator[RuntimeEvent]:
        record = self._require_record(request.turn_run_id)
        if record.state is RuntimeExecutionState.CANCELLED:
            raise _runtime_error(
                RuntimeErrorKind.CANCELLED,
                "runtime_turn_cancelled",
                "Turn 已取消，不能恢复",
            )
        if record.resume_request is not None:
            if record.resume_request != request:
                raise _runtime_error(
                    RuntimeErrorKind.PERMANENT,
                    "runtime_resume_conflict",
                    "同一 Turn 已使用不同恢复输入",
                )
            for event in record.resume_events:
                yield event
            return
        if record.state is not RuntimeExecutionState.INTERRUPTED:
            raise _runtime_error(
                RuntimeErrorKind.PERMANENT,
                "runtime_turn_not_interrupted",
                "只有 interrupted Turn 可以恢复",
            )

        record.resume_request = request
        start_sequence = record.execute_events[-1].sequence + 1
        record.resume_events = (
            self._event(
                record.request.turn_run_id,
                start_sequence,
                RuntimeEventKind.RESUMED,
                safe_summary="Fake Runtime 已恢复",
            ),
            self._event(
                record.request.turn_run_id,
                start_sequence + 1,
                RuntimeEventKind.ASSISTANT_DELTA,
                text_delta=record.result_plan.assistant_content,
            ),
            self._event(
                record.request.turn_run_id,
                start_sequence + 2,
                RuntimeEventKind.COMPLETED,
                safe_summary="Fake Runtime 已完成",
            ),
        )
        for event in record.resume_events:
            if record.state is RuntimeExecutionState.CANCELLED:
                return
            self._apply_event(record, event)
            record.last_event_sequence = event.sequence
            yield event

    async def cancel_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        """幂等取消非终态 Turn；成功终态保持不变。"""
        record = self._require_record(turn_run_id)
        if record.state not in {
            RuntimeExecutionState.SUCCEEDED,
            RuntimeExecutionState.FAILED,
            RuntimeExecutionState.CANCELLED,
        }:
            record.state = RuntimeExecutionState.CANCELLED
            record.result = None
        return self._reconciliation(record)

    async def reconcile_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        """返回 Runtime 自身状态与稳定 Binding。"""
        return self._reconciliation(self._require_record(turn_run_id))

    async def collect_turn_result(self, turn_run_id: str) -> RuntimeTurnResult:
        """成功后可重复读取相同结果，其他状态显式失败。"""
        record = self._require_record(turn_run_id)
        if record.state is RuntimeExecutionState.CANCELLED:
            raise _runtime_error(
                RuntimeErrorKind.CANCELLED,
                "runtime_turn_cancelled",
                "已取消 Turn 没有可提交结果",
            )
        if record.state is RuntimeExecutionState.INTERRUPTED:
            raise _runtime_error(
                RuntimeErrorKind.PERMANENT,
                "runtime_resume_required",
                "Turn 需要恢复后才能收集结果",
            )
        if record.state is not RuntimeExecutionState.SUCCEEDED or record.result is None:
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_result_not_ready",
                "Runtime 结果尚未就绪",
            )
        return record.result

    def _get_or_create_record(self, request: RuntimeTurnRequest) -> _TurnRecord:
        existing = self._turn_records.get(request.turn_run_id)
        if existing is not None:
            return existing

        session_binding = self._session_bindings.setdefault(
            request.session_id,
            RuntimeSessionBinding(
                session_id=request.session_id,
                binding_id=_opaque_id("binding", request.session_id),
                generation=1,
                runtime_thread_id=_opaque_id("thread", request.session_id),
                runtime_workspace_id=_opaque_id("workspace", request.session_id),
            ),
        )
        turn_binding = RuntimeTurnBinding(
            session_id=request.session_id,
            turn_run_id=request.turn_run_id,
            session_binding_id=session_binding.binding_id,
            runtime_execution_id=_opaque_id("execution", request.turn_run_id),
            runtime_checkpoint_id=_opaque_id("checkpoint", request.turn_run_id),
        )
        content_hash = hashlib.sha256(f"fake-candidate:{request.turn_run_id}".encode()).hexdigest()
        result = RuntimeTurnResult(
            turn_run_id=request.turn_run_id,
            assistant_content=self._response_for(request),
            artifact_candidates=(
                RuntimeArtifactCandidate(
                    candidate_id=_opaque_id("candidate", request.turn_run_id),
                    name="research-note.md",
                    media_type="text/markdown",
                    content_ref=f"fake-staged://{request.turn_run_id}/research-note.md",
                    content_hash=content_hash,
                    size_bytes=128,
                ),
            ),
        )
        final_kind = (
            RuntimeEventKind.INTERRUPTED
            if request.turn_run_id in self._interrupt_turn_ids
            else RuntimeEventKind.COMPLETED
        )
        events: list[RuntimeEvent] = [
            self._event(
                request.turn_run_id,
                1,
                RuntimeEventKind.BOUND,
                safe_summary="Fake Runtime 已建立稳定绑定",
            ),
            self._event(
                request.turn_run_id,
                2,
                RuntimeEventKind.STARTED,
                safe_summary="Fake Runtime 已开始",
            ),
        ]
        if final_kind is RuntimeEventKind.COMPLETED:
            events.append(
                self._event(
                    request.turn_run_id,
                    3,
                    RuntimeEventKind.ASSISTANT_DELTA,
                    text_delta=result.assistant_content,
                )
            )
        events.append(
            self._event(
                request.turn_run_id,
                len(events) + 1,
                final_kind,
                safe_summary=(
                    "Fake Runtime 等待恢复"
                    if final_kind is RuntimeEventKind.INTERRUPTED
                    else "Fake Runtime 已完成"
                ),
            )
        )
        record = _TurnRecord(
            request=request,
            session_binding=session_binding,
            turn_binding=turn_binding,
            execute_events=tuple(events),
            state=RuntimeExecutionState.RUNNING,
            result_plan=result,
        )
        self._turn_records[request.turn_run_id] = record
        self._execution_start_count += 1
        return record

    def _apply_event(self, record: _TurnRecord, event: RuntimeEvent) -> None:
        if event.kind in {RuntimeEventKind.BOUND, RuntimeEventKind.STARTED}:
            record.state = RuntimeExecutionState.RUNNING
        elif event.kind is RuntimeEventKind.INTERRUPTED:
            record.state = RuntimeExecutionState.INTERRUPTED
        elif event.kind is RuntimeEventKind.COMPLETED:
            record.state = RuntimeExecutionState.SUCCEEDED
            record.result = record.result_plan

    def _require_record(self, turn_run_id: str) -> _TurnRecord:
        record = self._turn_records.get(turn_run_id)
        if record is None:
            raise _runtime_error(
                RuntimeErrorKind.PERMANENT,
                "runtime_turn_not_found",
                "Runtime 中不存在指定 Turn",
            )
        return record

    def _reconciliation(self, record: _TurnRecord) -> RuntimeTurnReconciliation:
        return RuntimeTurnReconciliation(
            turn_run_id=record.request.turn_run_id,
            state=record.state,
            session_binding=record.session_binding,
            turn_binding=record.turn_binding,
            last_event_sequence=record.last_event_sequence,
            result_available=record.result is not None,
        )

    def _response_for(self, request: RuntimeTurnRequest) -> str:
        digest = hashlib.sha256(
            (
                f"{request.session_id}:{request.turn_run_id}:"
                f"{request.context_snapshot.snapshot_hash}:"
                f"{request.policy_snapshot.snapshot_hash}:"
                f"{request.user_message_content}"
            ).encode()
        ).hexdigest()[:16]
        return f"Fake Research Agent 确定性响应 [{digest}]"

    def _event(
        self,
        turn_run_id: str,
        sequence: int,
        kind: RuntimeEventKind,
        *,
        text_delta: str | None = None,
        safe_summary: str | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=_opaque_id("event", f"{turn_run_id}:{sequence}:{kind.value}"),
            turn_run_id=turn_run_id,
            sequence=sequence,
            kind=kind,
            text_delta=text_delta,
            safe_summary=safe_summary,
        )


def _opaque_id(kind: str, stable_id: str) -> str:
    digest = hashlib.sha256(f"fake:{kind}:{stable_id}".encode()).hexdigest()[:24]
    return f"fake-{kind}-{digest}"


def _runtime_error(
    kind: RuntimeErrorKind,
    code: str,
    safe_message: str,
) -> ResearchAgentRuntimeError:
    return ResearchAgentRuntimeError(kind=kind, code=code, safe_message=safe_message)
