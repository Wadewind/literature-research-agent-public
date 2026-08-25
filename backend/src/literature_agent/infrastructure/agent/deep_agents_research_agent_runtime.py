"""受限 Deep Agents 0.7 Adapter；SDK 类型只存在于 infrastructure。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.types import Command, StateSnapshot

from literature_agent.application.ports.project_research_context import (
    ProjectContextToolResult,
    ProjectResearchContext,
    ProjectResearchContextError,
)
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeExecutionState,
    RuntimeResumeRequest,
    RuntimeTurnReconciliation,
    RuntimeTurnRequest,
    RuntimeTurnResult,
)
from literature_agent.domain.agent_answer import parse_agent_answer
from literature_agent.domain.research_agent import (
    RuntimeSessionBinding,
    RuntimeTurnBinding,
)
from literature_agent.domain.tool_execution import ToolErrorKind, canonical_tool_args

_TURN_METADATA_KEY = "agent_runtime_turn_id"
_SESSION_METADATA_KEY = "agent_runtime_session_id"
_REQUEST_HASH_METADATA_KEY = "agent_runtime_request_hash"
_EXECUTION_METADATA_KEY = "agent_runtime_execution_id"
_FILESYSTEM_TOOL_NAMES = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "glob", "grep"}
)
_FORBIDDEN_TOOL_NAMES = frozenset({"execute", "task"})


@dataclass(frozen=True, slots=True)
class _TurnContext:
    """单次图调用上下文；不进入公开 Port 或业务数据库。"""

    turn_run_id: str
    allowed_tool_names: frozenset[str]


@dataclass(slots=True)
class _LocalTurn:
    """只用于在途取消/失败；成功事实始终从 Checkpoint 重建。"""

    request: RuntimeTurnRequest
    state: RuntimeExecutionState
    last_event_sequence: int
    checkpoint_id: str | None = None


class _RuntimeToolPolicyMiddleware(AgentMiddleware[Any, _TurnContext, Any]):
    """在最终模型调用边界收窄内置与平台工具可见性。"""

    def __init__(self, *, registered_tool_names: frozenset[str]) -> None:
        self._registered_tool_names = registered_tool_names

    def _allowed(self, context: _TurnContext) -> frozenset[str]:
        return context.allowed_tool_names & self._registered_tool_names

    def _filtered(
        self, request: ModelRequest[_TurnContext]
    ) -> list[BaseTool | dict[str, Any]]:
        allowed = self._allowed(request.runtime.context)
        return [tool for tool in request.tools if _tool_name(tool) in allowed]

    def wrap_model_call(
        self,
        request: ModelRequest[_TurnContext],
        handler: Callable[[ModelRequest[_TurnContext]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(request.override(tools=self._filtered(request)))

    async def awrap_model_call(
        self,
        request: ModelRequest[_TurnContext],
        handler: Callable[[ModelRequest[_TurnContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(request.override(tools=self._filtered(request)))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        self._require_allowed_tool(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command[Any]],
        ],
    ) -> ToolMessage | Command[Any]:
        self._require_allowed_tool(request)
        return await handler(request)

    def _require_allowed_tool(self, request: ToolCallRequest) -> None:
        context = cast(_TurnContext, request.runtime.context)
        name = request.tool_call.get("name")
        allowed = self._allowed(context)
        if name not in allowed:
            raise _runtime_error(
                RuntimeErrorKind.PERMANENT,
                "runtime_tool_not_allowed",
                "Runtime Tool 未被本轮策略授权",
            )


class DeepAgentsResearchAgentRuntime:
    """用原生 ``create_deep_agent`` 实现五方法业务 Port 的受限 Spike。"""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver[str],
        tools: Sequence[BaseTool] = (),
        project_context: ProjectResearchContext | None = None,
        summarization_trigger: tuple[str, int | float] = ("messages", 100),
        summarization_keep: tuple[str, int | float] = ("messages", 20),
    ) -> None:
        self._checkpointer = checkpointer
        self._local_turns: dict[str, _LocalTurn] = {}

        backend = StateBackend()
        self._register_restricted_harness_profile(model)
        project_tools = self._project_context_tools(project_context)
        all_tools = (*tools, *project_tools)
        names = [item.name for item in all_tools]
        if len(names) != len(set(names)):
            raise ValueError("Deep Agents Tool 名称不得重复")
        registered_tool_names = (
            _FILESYSTEM_TOOL_NAMES | frozenset(item.name for item in all_tools)
        ) - _FORBIDDEN_TOOL_NAMES
        filesystem = FilesystemMiddleware(
            backend=backend,
            tools=["ls", "read_file", "write_file", "edit_file", "glob", "grep"],
        )
        summarization = SummarizationMiddleware(
            model=model,
            backend=backend,
            trigger=cast(Any, summarization_trigger),
            keep=cast(Any, summarization_keep),
        )
        self._graph = create_deep_agent(
            model=model,
            tools=all_tools if project_context is not None else tools,
            system_prompt=(
                "你是绑定 Research Project 的受限研究助手。只使用当前允许的工具；"
                "不得声称访问了未提供的论文、网络、Sandbox 或外部系统。"
                "使用 Evidence 回答时，每个非空论述独占一行并严格以"
                " [evidence:<id>[,<id>...]] 结尾；证据不足时只输出"
                "‘当前授权上下文证据不足。’。"
            ),
            middleware=cast(
                Sequence[AgentMiddleware[Any, Any, Any]],
                (
                    filesystem,
                    summarization,
                    _RuntimeToolPolicyMiddleware(
                        registered_tool_names=registered_tool_names
                    ),
                ),
            ),
            subagents=[],
            skills=None,
            memory=None,
            backend=backend,
            context_schema=_TurnContext,
            checkpointer=checkpointer,
            name="project-research-agent",
        )

    def execute_turn(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        return self._execute_stream(request)

    async def _execute_stream(self, request: RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]:
        existing = await self._find_checkpoint(request.turn_run_id)
        if existing is not None:
            self._validate_existing_request(existing, request)
            reconciliation = await self._reconciliation_from_checkpoint(existing)
            if reconciliation.state is RuntimeExecutionState.SUCCEEDED:
                async for event in self._replay_succeeded(request.turn_run_id, existing):
                    yield event
            return

        local = self._local_turns.get(request.turn_run_id)
        if local is not None:
            if local.request != request:
                raise _runtime_error(
                    RuntimeErrorKind.PERMANENT,
                    "runtime_turn_conflict",
                    "同一 turn_run_id 已绑定不同输入",
                )
            if local.state is RuntimeExecutionState.CANCELLED:
                return
        else:
            local = _LocalTurn(request, RuntimeExecutionState.RUNNING, 0)
            self._local_turns[request.turn_run_id] = local

        local.last_event_sequence = 1
        yield self._event(request.turn_run_id, 1, RuntimeEventKind.BOUND, "Deep Agents 已绑定")
        if local.state is RuntimeExecutionState.CANCELLED:
            return
        config = self._config(request)
        context = _TurnContext(
            turn_run_id=request.turn_run_id,
            allowed_tool_names=frozenset(request.policy_snapshot.allowed_tool_names),
        )
        stream: AsyncIterator[Any] | None = None
        try:
            stream = self._graph.astream(
                {
                    "messages": [
                        HumanMessage(
                            content=request.user_message_content,
                            id=request.user_message_id,
                        )
                    ]
                },
                config,
                context=context,
                stream_mode="updates",
            )
            # Deep Agents 固定的 before_agent 首个更新会先形成真实 Checkpoint，
            # 但尚未进入模型调用；STARTED 后取消因此既可对账，也不会新增模型/Tool。
            await anext(stream)
            started_checkpoint = await self._find_checkpoint(request.turn_run_id)
            if started_checkpoint is not None:
                local.checkpoint_id = _checkpoint_id(started_checkpoint.config)
            local.last_event_sequence = 2
            yield self._event(
                request.turn_run_id,
                2,
                RuntimeEventKind.STARTED,
                "Deep Agents 已开始",
            )
            if local.state is RuntimeExecutionState.CANCELLED:
                return
            async for _ in stream:
                if local.state is RuntimeExecutionState.CANCELLED:
                    return
        except ResearchAgentRuntimeError:
            local.state = RuntimeExecutionState.FAILED
            raise
        except Exception as exc:
            local.state = RuntimeExecutionState.FAILED
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_execution_failed",
                "Deep Agents 执行暂时失败",
            ) from exc
        finally:
            if stream is not None:
                await _close_stream(stream)

        if local.state is RuntimeExecutionState.CANCELLED:
            return
        checkpoint = await self._find_checkpoint(request.turn_run_id)
        if checkpoint is None:
            local.state = RuntimeExecutionState.FAILED
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_checkpoint_missing",
                "Deep Agents 最终 Checkpoint 尚不可用",
            )
        reconciliation = await self._reconciliation_from_checkpoint(checkpoint)
        local.checkpoint_id = reconciliation.turn_binding.runtime_checkpoint_id
        if reconciliation.state is not RuntimeExecutionState.SUCCEEDED:
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_result_not_ready",
                "Deep Agents 结果尚未就绪",
            )
        local.state = RuntimeExecutionState.SUCCEEDED
        result = await self._result_from_checkpoint(checkpoint)
        local.last_event_sequence = 3
        yield RuntimeEvent(
            event_id=_opaque_id("event", f"{request.turn_run_id}:3:assistant_delta"),
            turn_run_id=request.turn_run_id,
            sequence=3,
            kind=RuntimeEventKind.ASSISTANT_DELTA,
            text_delta=result.assistant_content,
        )
        local.last_event_sequence = 4
        yield self._event(request.turn_run_id, 4, RuntimeEventKind.COMPLETED, "Deep Agents 已完成")

    def resume_turn(self, request: RuntimeResumeRequest) -> AsyncIterator[RuntimeEvent]:
        return self._unsupported_resume(request)

    async def _unsupported_resume(
        self, request: RuntimeResumeRequest
    ) -> AsyncIterator[RuntimeEvent]:
        reconciliation = await self.reconcile_turn(request.turn_run_id)
        if reconciliation.state is RuntimeExecutionState.CANCELLED:
            raise _runtime_error(
                RuntimeErrorKind.CANCELLED,
                "runtime_turn_cancelled",
                "Turn 已取消，不能恢复",
            )
        raise _runtime_error(
            RuntimeErrorKind.PERMANENT,
            "runtime_turn_not_interrupted",
            "本切片未配置可恢复 Interrupt",
        )
        yield  # pragma: no cover - 保持异步迭代器签名

    async def cancel_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        local = self._local_turns.get(turn_run_id)
        checkpoint = await self._find_checkpoint(turn_run_id)
        if local is None and checkpoint is None:
            raise _runtime_error(
                RuntimeErrorKind.PERMANENT,
                "runtime_turn_not_found",
                "Runtime 中不存在指定 Turn",
            )
        if checkpoint is not None:
            reconciliation = await self._reconciliation_from_checkpoint(checkpoint)
            if reconciliation.state is RuntimeExecutionState.SUCCEEDED:
                return reconciliation
        if local is None:
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_turn_not_active",
                "当前 Adapter 中没有可协作取消的在途执行",
            )
        local.state = RuntimeExecutionState.CANCELLED
        return self._local_reconciliation(local)

    async def reconcile_turn(self, turn_run_id: str) -> RuntimeTurnReconciliation:
        local = self._local_turns.get(turn_run_id)
        if local is not None and local.state in {
            RuntimeExecutionState.CANCELLED,
            RuntimeExecutionState.FAILED,
        }:
            return self._local_reconciliation(local)
        checkpoint = await self._find_checkpoint(turn_run_id)
        if checkpoint is not None:
            return await self._reconciliation_from_checkpoint(checkpoint)
        if local is not None:
            return self._local_reconciliation(local)
        raise _runtime_error(
            RuntimeErrorKind.PERMANENT,
            "runtime_turn_not_found",
            "Runtime 中不存在指定 Turn",
        )

    async def collect_turn_result(self, turn_run_id: str) -> RuntimeTurnResult:
        local = self._local_turns.get(turn_run_id)
        if local is not None and local.state is RuntimeExecutionState.CANCELLED:
            raise _runtime_error(
                RuntimeErrorKind.CANCELLED,
                "runtime_turn_cancelled",
                "已取消 Turn 没有可提交结果",
            )
        checkpoint = await self._find_checkpoint(turn_run_id)
        if checkpoint is None:
            if local is None:
                raise _runtime_error(
                    RuntimeErrorKind.PERMANENT,
                    "runtime_turn_not_found",
                    "Runtime 中不存在指定 Turn",
                )
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_result_not_ready",
                "Runtime 结果尚未就绪",
            )
        reconciliation = await self._reconciliation_from_checkpoint(checkpoint)
        if reconciliation.state is not RuntimeExecutionState.SUCCEEDED:
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_result_not_ready",
                "Runtime 结果尚未就绪",
            )
        return await self._result_from_checkpoint(checkpoint)

    async def _replay_succeeded(
        self, turn_run_id: str, checkpoint: CheckpointTuple
    ) -> AsyncIterator[RuntimeEvent]:
        result = await self._result_from_checkpoint(checkpoint)
        yield self._event(turn_run_id, 1, RuntimeEventKind.BOUND, "Deep Agents 已绑定")
        yield self._event(turn_run_id, 2, RuntimeEventKind.STARTED, "Deep Agents 已开始")
        yield RuntimeEvent(
            event_id=_opaque_id("event", f"{turn_run_id}:3:assistant_delta"),
            turn_run_id=turn_run_id,
            sequence=3,
            kind=RuntimeEventKind.ASSISTANT_DELTA,
            text_delta=result.assistant_content,
        )
        yield self._event(turn_run_id, 4, RuntimeEventKind.COMPLETED, "Deep Agents 已完成")

    async def _find_checkpoint(self, turn_run_id: str) -> CheckpointTuple | None:
        try:
            async for item in self._checkpointer.alist(
                None,
                filter={_TURN_METADATA_KEY: turn_run_id},
                limit=1,
            ):
                return item
            return None
        except asyncio.CancelledError:
            raise
        except ResearchAgentRuntimeError:
            raise
        except Exception as exc:
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_checkpoint_unavailable",
                "Runtime Checkpoint 暂时不可用",
            ) from exc

    async def _read_state(self, checkpoint: CheckpointTuple) -> StateSnapshot:
        try:
            return await self._graph.aget_state(checkpoint.config)
        except asyncio.CancelledError:
            raise
        except ResearchAgentRuntimeError:
            raise
        except Exception as exc:
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_checkpoint_unreadable",
                "Runtime Checkpoint 状态暂时不可读取",
            ) from exc

    async def _reconciliation_from_checkpoint(
        self, checkpoint: CheckpointTuple
    ) -> RuntimeTurnReconciliation:
        metadata = checkpoint.metadata
        turn_run_id = _required_metadata(metadata, _TURN_METADATA_KEY)
        session_id = _required_metadata(metadata, _SESSION_METADATA_KEY)
        snapshot = await self._read_state(checkpoint)
        state = (
            RuntimeExecutionState.INTERRUPTED
            if snapshot.interrupts
            else RuntimeExecutionState.SUCCEEDED
            if not snapshot.next and self._assistant_content(snapshot.values.get("messages", []))
            else RuntimeExecutionState.RUNNING
        )
        checkpoint_id = _checkpoint_id(checkpoint.config)
        return RuntimeTurnReconciliation(
            turn_run_id=turn_run_id,
            state=state,
            session_binding=self._session_binding(session_id),
            turn_binding=self._turn_binding(session_id, turn_run_id, checkpoint_id),
            last_event_sequence=4 if state is RuntimeExecutionState.SUCCEEDED else 2,
            result_available=state is RuntimeExecutionState.SUCCEEDED,
        )

    async def _result_from_checkpoint(self, checkpoint: CheckpointTuple) -> RuntimeTurnResult:
        turn_run_id = _required_metadata(checkpoint.metadata, _TURN_METADATA_KEY)
        snapshot = await self._read_state(checkpoint)
        messages = snapshot.values.get("messages", [])
        assistant_content = self._assistant_content(messages)
        if not assistant_content:
            raise _runtime_error(
                RuntimeErrorKind.TEMPORARY,
                "runtime_result_not_ready",
                "Runtime 结果尚未就绪",
            )
        evidence_ids: tuple[str, ...] = ()
        if "[evidence:" in assistant_content:
            try:
                _, evidence_ids = parse_agent_answer(assistant_content)
            except ValueError as exc:
                raise _runtime_error(
                    RuntimeErrorKind.PERMANENT,
                    "runtime_output_invalid",
                    "Deep Agents 最终回答的 Evidence 标记非法",
                ) from exc
        return RuntimeTurnResult(
            turn_run_id=turn_run_id,
            assistant_content=assistant_content,
            evidence_ids=evidence_ids,
        )

    @staticmethod
    def _project_context_tools(
        project_context: ProjectResearchContext | None,
    ) -> tuple[BaseTool, ...]:
        if project_context is None:
            return ()

        @tool
        async def search_project_chunks(
            query: str,
            runtime: ToolRuntime[_TurnContext],
        ) -> str:
            """在本轮冻结的 Project ChunkSet 中检索证据；只需提供研究问题。"""
            try:
                result = await project_context.search_project_chunks(
                    runtime.context.turn_run_id,
                    query=query,
                )
            except ProjectResearchContextError as exc:
                raise _runtime_error(
                    _runtime_kind(exc),
                    exc.code,
                    exc.safe_message,
                ) from exc
            return _tool_result_json(result)

        @tool
        async def read_review_evidence_matrix(
            runtime: ToolRuntime[_TurnContext],
        ) -> str:
            """读取本轮 ContextSnapshot 指定的 Review Evidence Matrix。"""
            try:
                result = await project_context.read_review_evidence_matrix(
                    runtime.context.turn_run_id
                )
            except ProjectResearchContextError as exc:
                raise _runtime_error(
                    _runtime_kind(exc),
                    exc.code,
                    exc.safe_message,
                ) from exc
            return _tool_result_json(result)

        return search_project_chunks, read_review_evidence_matrix

    def _local_reconciliation(self, local: _LocalTurn) -> RuntimeTurnReconciliation:
        request = local.request
        return RuntimeTurnReconciliation(
            turn_run_id=request.turn_run_id,
            state=local.state,
            session_binding=self._session_binding(request.session_id),
            turn_binding=self._turn_binding(
                request.session_id,
                request.turn_run_id,
                local.checkpoint_id or "checkpoint-not-created",
            ),
            last_event_sequence=local.last_event_sequence,
            result_available=False,
        )

    def _validate_existing_request(
        self, checkpoint: CheckpointTuple, request: RuntimeTurnRequest
    ) -> None:
        metadata = checkpoint.metadata
        if (
            metadata.get(_SESSION_METADATA_KEY) != request.session_id
            or metadata.get(_REQUEST_HASH_METADATA_KEY) != _request_hash(request)
        ):
            raise _runtime_error(
                RuntimeErrorKind.PERMANENT,
                "runtime_turn_conflict",
                "同一 turn_run_id 已绑定不同输入",
            )

    def _config(self, request: RuntimeTurnRequest) -> RunnableConfig:
        execution_id = _opaque_id("execution", request.turn_run_id)
        return {
            "configurable": {"thread_id": _opaque_id("thread", request.session_id)},
            "metadata": {
                _TURN_METADATA_KEY: request.turn_run_id,
                _SESSION_METADATA_KEY: request.session_id,
                _REQUEST_HASH_METADATA_KEY: _request_hash(request),
                _EXECUTION_METADATA_KEY: execution_id,
            },
        }

    def _session_binding(self, session_id: str) -> RuntimeSessionBinding:
        return RuntimeSessionBinding(
            session_id=session_id,
            binding_id=_opaque_id("binding", session_id),
            generation=1,
            runtime_thread_id=_opaque_id("thread", session_id),
            runtime_workspace_id=_opaque_id("workspace", session_id),
        )

    def _turn_binding(
        self, session_id: str, turn_run_id: str, checkpoint_id: str
    ) -> RuntimeTurnBinding:
        session_binding = self._session_binding(session_id)
        return RuntimeTurnBinding(
            session_id=session_id,
            turn_run_id=turn_run_id,
            session_binding_id=session_binding.binding_id,
            runtime_execution_id=_opaque_id("execution", turn_run_id),
            runtime_checkpoint_id=checkpoint_id,
        )

    @staticmethod
    def _assistant_content(messages: Sequence[Any]) -> str | None:
        for message in reversed(messages):
            if isinstance(message, AIMessage) and not message.tool_calls and message.text.strip():
                return message.text
        return None

    @staticmethod
    def _register_restricted_harness_profile(model: BaseChatModel) -> None:
        params = model._get_ls_params()  # noqa: SLF001 - LangChain 的 Provider 识别扩展点
        provider = params.get("ls_provider")
        model_name = params.get("ls_model_name")
        if not _valid_profile_component(provider) or not _valid_profile_component(
            model_name
        ):
            raise ValueError(
                "Deep Agents model 必须提供可用于精确 Profile 的 ls_provider/ls_model_name"
            )
        resolved_identifier = getattr(model, "model_name", None) or getattr(
            model, "model", None
        )
        if resolved_identifier != model_name:
            raise ValueError(
                "Deep Agents model 的 ls_model_name 必须匹配可解析的模型标识"
            )
        register_harness_profile(
            f"{provider}:{model_name}",
            HarnessProfile(
                excluded_tools=frozenset({"execute"}),
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )

    @staticmethod
    def _event(
        turn_run_id: str,
        sequence: int,
        kind: RuntimeEventKind,
        safe_summary: str,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=_opaque_id("event", f"{turn_run_id}:{sequence}:{kind.value}"),
            turn_run_id=turn_run_id,
            sequence=sequence,
            kind=kind,
            safe_summary=safe_summary,
        )


def _tool_name(tool: BaseTool | dict[str, Any]) -> str | None:
    name = tool.get("name") if isinstance(tool, dict) else tool.name
    return name if isinstance(name, str) else None


def _valid_profile_component(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and ":" not in value
    )


def _request_hash(request: RuntimeTurnRequest) -> str:
    payload = {
        "session_id": request.session_id,
        "turn_run_id": request.turn_run_id,
        "user_message_id": request.user_message_id,
        "user_message_sha256": hashlib.sha256(request.user_message_content.encode()).hexdigest(),
        "context_snapshot_hash": request.context_snapshot.snapshot_hash,
        "policy_snapshot_hash": request.policy_snapshot.snapshot_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _opaque_id(kind: str, stable_input: str) -> str:
    digest = hashlib.sha256(f"deepagents:{kind}:{stable_input}".encode()).hexdigest()[:32]
    return f"deepagents-{kind}-{digest}"


async def _close_stream(stream: AsyncIterator[Any]) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


def _required_metadata(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise _runtime_error(
            RuntimeErrorKind.PERMANENT,
            "runtime_checkpoint_invalid",
            "Deep Agents Checkpoint metadata 不完整",
        )
    return value


def _checkpoint_id(config: RunnableConfig) -> str:
    value = config.get("configurable", {}).get("checkpoint_id")
    if not isinstance(value, str) or not value:
        raise _runtime_error(
            RuntimeErrorKind.PERMANENT,
            "runtime_checkpoint_invalid",
            "Deep Agents Checkpoint ID 不完整",
        )
    return value


def _runtime_error(
    kind: RuntimeErrorKind, code: str, safe_message: str
) -> ResearchAgentRuntimeError:
    return ResearchAgentRuntimeError(kind=kind, code=code, safe_message=safe_message)


def _runtime_kind(error: ProjectResearchContextError) -> RuntimeErrorKind:
    return {
        ToolErrorKind.TEMPORARY: RuntimeErrorKind.TEMPORARY,
        ToolErrorKind.PERMANENT: RuntimeErrorKind.PERMANENT,
        ToolErrorKind.CANCELLED: RuntimeErrorKind.CANCELLED,
    }[error.kind]


def _tool_result_json(result: ProjectContextToolResult) -> str:
    return canonical_tool_args(
        {
            "effect_id": result.effect_id,
            "result_hash": result.result_hash,
            **result.payload,
        }
    )
