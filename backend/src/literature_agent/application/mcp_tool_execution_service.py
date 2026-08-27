"""MCP Tool 的平台授权、Effectively Once 账本与安全 Event。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
)
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.tool_execution_repository import ToolExecutionRepository
from literature_agent.domain.event import create_event
from literature_agent.domain.research_agent import PolicySnapshot
from literature_agent.domain.run import Run, RunStatus, RunType
from literature_agent.domain.tool_execution import (
    ToolErrorKind,
    ToolExecution,
    ToolExecutionStatus,
    create_tool_execution,
)

TSession = TypeVar("TSession", bound=Session)


class McpToolExecutionService[TSession: Session]:
    """外部 MCP 调用前后各用独立短事务，不保存原始参数。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        tool_execution_repo_factory: Callable[[TSession], ToolExecutionRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._agent_repo_factory = agent_repo_factory
        self._tool_repo_factory = tool_execution_repo_factory
        self._event_repo_factory = event_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def assert_active(self, turn_run_id: str) -> None:
        async with self._session_factory() as session:
            run, _ = await self._load_scope(session, turn_run_id, lock=False, tool_name=None)
            self._require_running(run)

    async def begin(
        self,
        turn_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
    ) -> dict[str, Any] | None:
        proposed = create_tool_execution(
            turn_run_id=turn_run_id,
            tool_name=tool_name,
            arguments=arguments,
            invocation_id=invocation_id,
        )
        async with self._session_factory() as session:
            run, policy = await self._load_scope(
                session, turn_run_id, lock=True, tool_name=tool_name
            )
            self._require_running(run)
            repo = self._tool_repo_factory(session)
            existing = await repo.get(proposed.effect_id)
            if existing is not None:
                self._require_same_invocation(existing, proposed)
                if existing.status is ToolExecutionStatus.SUCCEEDED:
                    await session.commit()
                    return existing.result_payload
                if existing.status is ToolExecutionStatus.RUNNING:
                    raise _error(
                        "runtime_mcp_effect_in_progress",
                        "相同 MCP Tool effect 正在执行",
                        RuntimeErrorKind.TEMPORARY,
                    )
                if existing.error_kind is not ToolErrorKind.TEMPORARY:
                    raise _error(
                        existing.error_code or "runtime_mcp_effect_failed",
                        existing.safe_message or "相同 MCP Tool effect 已失败",
                        RuntimeErrorKind.CANCELLED
                        if existing.error_kind is ToolErrorKind.CANCELLED
                        else RuntimeErrorKind.PERMANENT,
                    )
                execution = existing.retry()
                if not await repo.save(
                    execution,
                    expected_status=ToolExecutionStatus.FAILED,
                    expected_attempt_count=existing.attempt_count,
                ):
                    raise _error(
                        "runtime_mcp_effect_in_progress",
                        "相同 MCP Tool effect 已被其他执行者认领",
                        RuntimeErrorKind.TEMPORARY,
                    )
            else:
                if await repo.count_by_turn(turn_run_id) >= policy.max_tool_calls:
                    raise _error("runtime_mcp_tool_budget_exceeded", "本轮 Tool 调用预算已耗尽")
                execution = await repo.add(proposed)
            await self._append_event(session, run, execution, "agent_tool_started", {})
            await session.commit()
        await notify_run_event(self._event_notifier, turn_run_id)
        return None

    async def succeed(
        self,
        turn_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result_payload: dict[str, Any],
        *,
        invocation_id: str,
    ) -> None:
        proposed = create_tool_execution(
            turn_run_id=turn_run_id,
            tool_name=tool_name,
            arguments=arguments,
            invocation_id=invocation_id,
        )
        async with self._session_factory() as session:
            run, _ = await self._load_scope(session, turn_run_id, lock=True, tool_name=tool_name)
            self._require_running(run)
            repo = self._tool_repo_factory(session)
            current = await repo.get(proposed.effect_id)
            if current is None:
                raise _error("runtime_mcp_effect_missing", "MCP Tool effect 不存在")
            self._require_same_invocation(current, proposed)
            if current.status is ToolExecutionStatus.SUCCEEDED:
                if current.result_payload != result_payload:
                    raise _error("runtime_mcp_result_conflict", "MCP Tool 结果发生冲突")
                return
            completed = current.succeed(result_payload)
            if not await repo.save(
                completed,
                expected_status=ToolExecutionStatus.RUNNING,
                expected_attempt_count=current.attempt_count,
            ):
                raise _error(
                    "runtime_mcp_effect_conflict",
                    "MCP Tool effect 提交冲突",
                    RuntimeErrorKind.TEMPORARY,
                )
            await self._append_event(
                session,
                run,
                completed,
                "agent_tool_completed",
                {"result_hash": completed.result_hash},
            )
            await session.commit()
        await notify_run_event(self._event_notifier, turn_run_id)

    async def fail(
        self,
        turn_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
        kind: ToolErrorKind,
        code: str,
        safe_message: str,
    ) -> None:
        proposed = create_tool_execution(
            turn_run_id=turn_run_id,
            tool_name=tool_name,
            arguments=arguments,
            invocation_id=invocation_id,
        )
        async with self._session_factory() as session:
            run, _ = await self._load_scope(session, turn_run_id, lock=True, tool_name=tool_name)
            repo = self._tool_repo_factory(session)
            current = await repo.get(proposed.effect_id)
            if current is None:
                return
            self._require_same_invocation(current, proposed)
            if current.status is not ToolExecutionStatus.RUNNING:
                return
            failed = current.fail(kind, code, safe_message)
            if not await repo.save(
                failed,
                expected_status=ToolExecutionStatus.RUNNING,
                expected_attempt_count=current.attempt_count,
            ):
                return
            await self._append_event(
                session,
                run,
                failed,
                "agent_tool_failed",
                {"error_kind": kind.value, "error_code": code},
            )
            await session.commit()
        await notify_run_event(self._event_notifier, turn_run_id)

    @staticmethod
    def _require_same_invocation(current: ToolExecution, proposed: ToolExecution) -> None:
        if (
            current.turn_run_id != proposed.turn_run_id
            or current.tool_name != proposed.tool_name
            or current.args_hash != proposed.args_hash
        ):
            raise _error(
                "runtime_mcp_invocation_conflict",
                "MCP Tool 逻辑调用身份发生冲突",
            )

    async def _load_scope(
        self,
        session: TSession,
        turn_run_id: str,
        *,
        lock: bool,
        tool_name: str | None,
    ) -> tuple[Run, PolicySnapshot]:
        run_repo = self._run_repo_factory(session)
        raw = await run_repo.get_by_id(turn_run_id)
        if raw is None:
            raise _error("runtime_mcp_scope_invalid", "Agent Turn 作用域非法")
        run = await run_repo.get_by_id_for_update(turn_run_id, raw.owner_id) if lock else raw
        if run is None or run.run_type != RunType.AGENT_TURN.value:
            raise _error("runtime_mcp_scope_invalid", "Agent Turn 作用域非法")
        agent_repo = self._agent_repo_factory(session)
        turn = await agent_repo.get_turn_scoped(turn_run_id, run.owner_id)
        if turn is None:
            raise _error("runtime_mcp_scope_invalid", "Agent Turn 作用域非法")
        agent_session = await agent_repo.get_session_scoped(turn.session_id, run.owner_id)
        policy = await agent_repo.get_policy_snapshot(turn.policy_snapshot_id)
        if (
            agent_session is None
            or policy is None
            or agent_session.project_id != run.project_id
            or policy.owner_id != run.owner_id
            or policy.project_id != run.project_id
            or policy.session_id != turn.session_id
            or policy.turn_run_id != turn_run_id
        ):
            raise _error("runtime_mcp_scope_invalid", "Agent MCP 授权闭包非法")
        if tool_name is not None and tool_name not in {
            tool.name for ref in policy.mcp_refs for tool in ref.tools
        }:
            raise _error("runtime_mcp_tool_not_allowed", "MCP Tool 未被本轮策略授权")
        return run, policy

    @staticmethod
    def _require_running(run: Run) -> None:
        if run.status is RunStatus.CANCEL_REQUESTED:
            raise _error(
                "runtime_mcp_cancelled",
                "Agent Turn 已请求取消",
                RuntimeErrorKind.CANCELLED,
            )
        if run.status is not RunStatus.RUNNING:
            raise _error("runtime_mcp_turn_not_running", "Agent Turn 当前不可调用 MCP Tool")

    async def _append_event(
        self,
        session: TSession,
        run: Run,
        execution: ToolExecution,
        event_type: str,
        extra: dict[str, Any],
    ) -> None:
        await self._event_repo_factory(session).add(
            create_event(
                run.run_id,
                run.event_sequence,
                event_type,
                "system",
                f"tool:{execution.effect_id}",
                {
                    "tool_name": execution.tool_name,
                    "effect_id": execution.effect_id,
                    "status": execution.status.value,
                    "attempt_count": execution.attempt_count,
                    **extra,
                },
            )
        )
        if not await self._run_repo_factory(session).update_status(
            run.run_id,
            run.status,
            run.status,
            run.event_sequence + 1,
        ):
            raise _error(
                "runtime_mcp_event_conflict",
                "MCP Tool Event 序号发生并发冲突",
                RuntimeErrorKind.TEMPORARY,
            )


def _error(
    code: str,
    safe_message: str,
    kind: RuntimeErrorKind = RuntimeErrorKind.PERMANENT,
) -> ResearchAgentRuntimeError:
    return ResearchAgentRuntimeError(kind=kind, code=code, safe_message=safe_message)
