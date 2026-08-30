"""Agent Runtime 的持久化预算、取消与授权闭包守卫。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime
from typing import TypeVar

from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.agent_usage_control import (
    RuntimeBudget,
    ToolCallReservationRequest,
)
from literature_agent.application.ports.agent_usage_repository import AgentUsageRepository
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.agent_usage import (
    AgentToolCall,
    AgentToolCallStatus,
    AgentTurnUsage,
    create_agent_model_call_reservation,
    create_agent_tool_call,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.run import Run, RunStatus, RunType

TSession = TypeVar("TSession", bound=Session)


class AgentUsageError(RuntimeError):
    """可安全暴露的稳定预算/策略拒绝。"""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class AgentUsageService[TSession: Session]:
    """以短事务原子预留调用；同一 reservation replay 不重复计数。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        usage_repo_factory: Callable[[TSession], AgentUsageRepository],
        event_repo_factory: Callable[[TSession], EventRepository] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._agent_repo_factory = agent_repo_factory
        self._usage_repo_factory = usage_repo_factory
        self._event_repo_factory = event_repo_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def start_turn(self, turn_run_id: str) -> RuntimeBudget:
        async with self._session_factory() as session:
            usage, _, _ = await self._load_locked_closure(session, turn_run_id)
            now = self._clock()
            started = usage.start(now=now)
            self._require_before_deadline(started, now)
            if started != usage:
                await self._usage_repo_factory(session).save_usage(started)
            await session.commit()
            assert started.deadline_at is not None
            return RuntimeBudget(
                deadline_at=started.deadline_at,
                tool_timeout_seconds=started.tool_timeout_seconds,
                execute_timeout_seconds=started.execute_timeout_seconds,
                max_tool_output_bytes=started.max_tool_output_bytes,
                max_input_tokens_per_model_call=started.max_input_tokens_per_model_call,
                max_output_tokens_per_model_call=started.max_output_tokens_per_model_call,
            )

    async def reserve_model_call(
        self, turn_run_id: str, ordinal: int, *, approximate_input_tokens: int
    ) -> AgentTurnUsage:
        async with self._session_factory() as session:
            usage, _, run = await self._load_locked_closure(session, turn_run_id)
            now = self._clock()
            usage = usage.start(now=now)
            self._require_before_deadline(usage, now)
            if approximate_input_tokens > usage.max_input_tokens_per_model_call:
                raise AgentUsageError(
                    "agent_input_token_budget_exceeded",
                    "本次模型输入超过近似 Token 上限",
                )
            proposed = create_agent_model_call_reservation(
                turn_run_id=turn_run_id, ordinal=ordinal, now=now
            )
            repo = self._usage_repo_factory(session)
            existing = await repo.get_model_call(proposed.reservation_key)
            if existing is not None:
                await session.commit()
                return usage
            if usage.model_calls_reserved >= usage.max_model_calls:
                raise AgentUsageError("agent_model_budget_exceeded", "本轮模型调用预算已耗尽")
            await repo.add_model_call(proposed)
            updated = replace(
                usage,
                model_calls_reserved=usage.model_calls_reserved + 1,
                updated_at=now,
            )
            await repo.save_usage(updated)
            await self._append_budget_event(
                session,
                run,
                correlation_id=proposed.reservation_key,
                payload={
                    "model_calls_reserved": updated.model_calls_reserved,
                    "max_model_calls": updated.max_model_calls,
                    "tool_calls_reserved": updated.tool_calls_reserved,
                    "max_tool_calls": updated.max_tool_calls,
                },
            )
            await session.commit()
            return updated

    async def record_model_usage(
        self,
        turn_run_id: str,
        ordinal: int,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        key = f"model:{turn_run_id}:{ordinal}"
        async with self._session_factory() as session:
            usage, _, _ = await self._load_locked_closure(session, turn_run_id)
            repo = self._usage_repo_factory(session)
            reservation = await repo.get_model_call(key)
            if reservation is None:
                raise AgentUsageError("agent_model_reservation_missing", "模型调用尚未预留")
            try:
                recorded = reservation.record_tokens(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    now=self._clock(),
                )
            except ValueError as exc:
                raise AgentUsageError(
                    "agent_model_usage_conflict",
                    "模型 Token Usage 重放发生冲突",
                ) from exc
            if recorded != reservation:
                await repo.save_model_call(recorded)
                input_delta = input_tokens if reservation.input_tokens is None else None
                output_delta = output_tokens if reservation.output_tokens is None else None
                await repo.save_usage(
                    replace(
                        usage,
                        input_tokens=_add_optional(usage.input_tokens, input_delta),
                        output_tokens=_add_optional(usage.output_tokens, output_delta),
                        updated_at=self._clock(),
                    )
                )
            await session.commit()

    async def reserve_tool_call(
        self, turn_run_id: str, request: ToolCallReservationRequest
    ) -> AgentToolCall:
        async with self._session_factory() as session:
            usage, policy, run = await self._load_locked_closure(session, turn_run_id)
            now = self._clock()
            usage = usage.start(now=now)
            self._require_before_deadline(usage, now)
            if request.tool_name not in policy.allowed_tool_names:
                raise AgentUsageError("agent_tool_not_allowed", "本轮未授权该 Tool")
            expected = {ref.name: ref for ref in policy.tool_refs}.get(request.tool_name)
            if expected is None:
                raise AgentUsageError(
                    "agent_tool_contract_missing", "PolicySnapshot 缺少 Tool 固定契约"
                )
            if request.input_schema_hash != expected.input_schema_hash:
                raise AgentUsageError(
                    "agent_tool_schema_drift", "Runtime Tool Schema 与 PolicySnapshot 不一致"
                )
            proposed = create_agent_tool_call(
                turn_run_id=turn_run_id,
                invocation_id=request.invocation_id,
                tool_name=request.tool_name,
                tool_version=expected.version,
                input_schema_hash=request.input_schema_hash,
                args_hash=request.args_hash,
                input_size_bytes=request.input_size_bytes,
                input_preview=request.input_preview,
                input_preview_truncated=request.input_preview_truncated,
                now=now,
            )
            repo = self._usage_repo_factory(session)
            existing = await repo.get_tool_call(proposed.reservation_key)
            if existing is not None:
                if existing != proposed and (
                    existing.tool_name,
                    existing.input_schema_hash,
                    existing.args_hash,
                    existing.input_size_bytes,
                    existing.input_preview,
                    existing.input_preview_truncated,
                ) != (
                    proposed.tool_name,
                    proposed.input_schema_hash,
                    proposed.args_hash,
                    proposed.input_size_bytes,
                    proposed.input_preview,
                    proposed.input_preview_truncated,
                ):
                    raise AgentUsageError(
                        "agent_tool_reservation_conflict", "Tool reservation 重放发生冲突"
                    )
                await session.commit()
                return existing
            if usage.tool_calls_reserved >= usage.max_tool_calls:
                raise AgentUsageError("agent_tool_budget_exceeded", "本轮 Tool 调用预算已耗尽")
            repeated = await repo.count_tool_calls_by_signature(
                turn_run_id, request.tool_name, request.args_hash
            )
            if repeated >= usage.max_repeated_tool_calls:
                raise AgentUsageError("agent_tool_loop_rejected", "相同 Tool 调用已达到循环上限")
            await repo.add_tool_call(proposed)
            updated = replace(
                usage,
                tool_calls_reserved=usage.tool_calls_reserved + 1,
                updated_at=now,
            )
            await repo.save_usage(updated)
            await self._append_budget_event(
                session,
                run,
                correlation_id=proposed.reservation_key,
                payload={
                    "model_calls_reserved": updated.model_calls_reserved,
                    "max_model_calls": updated.max_model_calls,
                    "tool_calls_reserved": updated.tool_calls_reserved,
                    "max_tool_calls": updated.max_tool_calls,
                },
            )
            await session.commit()
            return proposed

    async def start_tool_call(self, turn_run_id: str, reservation_key: str) -> AgentToolCall:
        async with self._session_factory() as session:
            usage, _, _ = await self._load_locked_closure(session, turn_run_id)
            self._require_before_deadline(usage, self._clock())
            repo = self._usage_repo_factory(session)
            value = await self._require_tool_call(repo, turn_run_id, reservation_key)
            if value.status is AgentToolCallStatus.RUNNING:
                raise AgentUsageError(
                    "agent_tool_effect_in_progress",
                    "Tool 调用已被认领，必须从既有 effect 或 Checkpoint 对账",
                )
            if value.status in {
                AgentToolCallStatus.SUCCEEDED,
                AgentToolCallStatus.FAILED,
            }:
                raise AgentUsageError(
                    "agent_tool_effect_terminal",
                    "Tool 调用已结束，禁止重新执行副作用",
                )
            started = value.start(now=self._clock())
            if not await repo.save_tool_call(started, expected_status=AgentToolCallStatus.RESERVED):
                raise AgentUsageError("agent_tool_claim_conflict", "Tool 调用已被其他 Worker 认领")
            await session.commit()
            return started

    async def succeed_tool_call(
        self,
        turn_run_id: str,
        reservation_key: str,
        *,
        output_size_bytes: int,
        result_hash: str,
        output_preview: str | None = None,
        output_preview_truncated: bool = False,
    ) -> AgentToolCall:
        async with self._session_factory() as session:
            usage, _, _ = await self._load_locked_closure(session, turn_run_id)
            if output_size_bytes > usage.max_tool_output_bytes:
                raise AgentUsageError("agent_tool_output_too_large", "Tool 输出超过安全上限")
            repo = self._usage_repo_factory(session)
            value = await self._require_tool_call(repo, turn_run_id, reservation_key)
            if value.status is AgentToolCallStatus.SUCCEEDED:
                if (
                    value.output_size_bytes != output_size_bytes
                    or value.result_hash != result_hash
                    or value.output_preview != output_preview
                    or value.output_preview_truncated != output_preview_truncated
                ):
                    raise AgentUsageError("agent_tool_result_conflict", "Tool 成功结果重放发生冲突")
                await session.commit()
                return value
            if value.status is not AgentToolCallStatus.RUNNING:
                raise AgentUsageError("agent_tool_complete_conflict", "Tool 调用当前不能标记成功")
            succeeded = value.succeed(
                output_size_bytes=output_size_bytes,
                result_hash=result_hash,
                output_preview=output_preview,
                output_preview_truncated=output_preview_truncated,
                now=self._clock(),
            )
            if not await repo.save_tool_call(
                succeeded, expected_status=AgentToolCallStatus.RUNNING
            ):
                raise AgentUsageError("agent_tool_complete_conflict", "Tool 调用完成发生并发冲突")
            await session.commit()
            return succeeded

    async def fail_tool_call(
        self,
        turn_run_id: str,
        reservation_key: str,
        *,
        error_code: str,
        safe_message: str,
        output_preview: str | None = None,
        output_preview_truncated: bool = False,
    ) -> AgentToolCall:
        async with self._session_factory() as session:
            await self._load_locked_closure(session, turn_run_id, allow_cancelled=True)
            repo = self._usage_repo_factory(session)
            value = await self._require_tool_call(repo, turn_run_id, reservation_key)
            if value.status is AgentToolCallStatus.FAILED:
                if (
                    value.error_code != error_code
                    or value.safe_message != safe_message
                    or value.output_preview != output_preview
                    or value.output_preview_truncated != output_preview_truncated
                ):
                    raise AgentUsageError("agent_tool_error_conflict", "Tool 失败结果重放发生冲突")
                await session.commit()
                return value
            if value.status is not AgentToolCallStatus.RUNNING:
                raise AgentUsageError("agent_tool_complete_conflict", "Tool 调用当前不能标记失败")
            failed = value.fail(
                error_code=error_code,
                safe_message=safe_message,
                output_preview=output_preview,
                output_preview_truncated=output_preview_truncated,
                now=self._clock(),
            )
            if not await repo.save_tool_call(failed, expected_status=AgentToolCallStatus.RUNNING):
                raise AgentUsageError("agent_tool_complete_conflict", "Tool 调用完成发生并发冲突")
            await session.commit()
            return failed

    async def _load_locked_closure(
        self,
        session: TSession,
        turn_run_id: str,
        *,
        allow_cancelled: bool = False,
    ):
        repo = self._usage_repo_factory(session)
        raw = await repo.get_usage(turn_run_id)
        if raw is None:
            raise AgentUsageError("agent_usage_missing", "Agent Turn 预算事实不存在")
        run = await self._run_repo_factory(session).get_by_id_for_update(turn_run_id, raw.owner_id)
        usage = await repo.get_usage_for_update(turn_run_id)
        if run is None or usage is None or run.run_type != RunType.AGENT_TURN.value:
            raise AgentUsageError("agent_usage_scope_invalid", "Agent Turn 预算作用域非法")
        if not allow_cancelled:
            self._require_running(run)
        agent_repo = self._agent_repo_factory(session)
        turn = await agent_repo.get_turn_scoped(turn_run_id, usage.owner_id)
        agent_session = (
            await agent_repo.get_session_scoped(turn.session_id, usage.owner_id)
            if turn is not None
            else None
        )
        context = (
            await agent_repo.get_context_snapshot(turn.context_snapshot_id)
            if turn is not None
            else None
        )
        policy = (
            await agent_repo.get_policy_snapshot(turn.policy_snapshot_id)
            if turn is not None
            else None
        )
        if (
            turn is None
            or agent_session is None
            or context is None
            or policy is None
            or run.owner_id != usage.owner_id
            or run.project_id != usage.project_id
            or turn.session_id != usage.session_id
            or turn.policy_snapshot_id != usage.policy_snapshot_id
            or agent_session.project_id != usage.project_id
            or context.owner_id != usage.owner_id
            or context.project_id != usage.project_id
            or context.session_id != usage.session_id
            or context.turn_run_id != turn_run_id
            or policy.owner_id != usage.owner_id
            or policy.project_id != usage.project_id
            or policy.session_id != usage.session_id
            or policy.turn_run_id != turn_run_id
            or policy.max_model_calls != usage.max_model_calls
            or policy.max_tool_calls != usage.max_tool_calls
            or policy.wall_clock_limit_seconds != usage.wall_clock_limit_seconds
            or policy.tool_timeout_seconds != usage.tool_timeout_seconds
            or policy.execute_timeout_seconds != usage.execute_timeout_seconds
            or policy.max_tool_output_bytes != usage.max_tool_output_bytes
            or policy.max_repeated_tool_calls != usage.max_repeated_tool_calls
            or policy.max_input_tokens_per_model_call != usage.max_input_tokens_per_model_call
            or policy.max_output_tokens_per_model_call != usage.max_output_tokens_per_model_call
        ):
            raise AgentUsageError("agent_usage_scope_invalid", "Agent 授权闭包或 Policy 已漂移")
        return usage, policy, run

    async def _append_budget_event(
        self,
        session: TSession,
        run: Run,
        *,
        correlation_id: str,
        payload: dict[str, int],
    ) -> None:
        """仅记录计数/上限；Prompt、Tool 参数和结果均不进入 Event。"""
        if self._event_repo_factory is None:
            return
        await self._event_repo_factory(session).add(
            create_event(
                run.run_id,
                run.event_sequence,
                "agent_budget_updated",
                "system",
                correlation_id,
                payload,
            )
        )
        if not await self._run_repo_factory(session).update_status(
            run.run_id,
            run.status,
            run.status,
            run.event_sequence + 1,
        ):
            raise AgentUsageError("agent_budget_event_conflict", "Agent Budget Event 发生并发冲突")

    @staticmethod
    def _require_running(run: Run) -> None:
        if run.status is RunStatus.CANCEL_REQUESTED:
            raise AgentUsageError("agent_turn_cancelled", "Agent Turn 已请求取消")
        if run.status is not RunStatus.RUNNING:
            raise AgentUsageError("agent_turn_not_running", "Agent Turn 当前不可调用模型或 Tool")

    @staticmethod
    def _require_before_deadline(usage: AgentTurnUsage, now: datetime) -> None:
        if usage.deadline_at is not None and now >= usage.deadline_at:
            raise AgentUsageError("agent_turn_deadline_exceeded", "Agent Turn 已超过墙钟预算")

    @staticmethod
    async def _require_tool_call(
        repo: AgentUsageRepository, turn_run_id: str, reservation_key: str
    ) -> AgentToolCall:
        value = await repo.get_tool_call(reservation_key)
        if value is None or value.turn_run_id != turn_run_id:
            raise AgentUsageError("agent_tool_reservation_missing", "Tool 调用尚未预留")
        return value


def _add_optional(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    return (current or 0) + value
