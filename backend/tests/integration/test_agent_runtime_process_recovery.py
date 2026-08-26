"""真实 OS 进程退出后的 Deep Agents Checkpoint 恢复证据。"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from pydantic import Field
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from literature_agent.application.agent_turn_executor import AgentTurnExecutor
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlService,
)
from literature_agent.domain.research_agent import (
    AgentMessageRole,
    create_agent_message,
    create_agent_session,
    create_agent_turn_run,
    create_context_snapshot,
    create_policy_snapshot,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    DeepAgentsResearchAgentRuntime,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from literature_agent.infrastructure.persistence.runtime_execution_repository import (
    SqlalchemyRuntimeExecutionRepository,
)
from literature_agent.infrastructure.workflow.postgres_checkpoint import (
    PostgresCheckpointStore,
)
from tests.fakes.agent_scenario import seed_agent_scenario

_TOOL_NAME = "record_research_step"


def _append_audit(path: str, value: str) -> None:
    """跨进程刷新一条可观察记录，不记录 Prompt 或 Tool 参数。"""
    with open(path, "a", encoding="utf-8") as handle:  # noqa: PTH123
        handle.write(f"{value}\n")
        handle.flush()
        os.fsync(handle.fileno())


class _ProcessRecoveryModel(BaseChatModel):
    """第二次模型调用可阻塞，以制造 Tool Step 已 checkpoint 的崩溃点。"""

    model_name: str = "phase5-process-recovery-fake"
    audit_path: str
    block_final: bool = False
    visible_tool_names: list[tuple[str, ...]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "phase5-process-recovery-fake"

    def bind_tools(
        self,
        tools: list[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        del tool_choice, kwargs
        self.visible_tool_names.append(tuple(item.name for item in tools))
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._message(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        if any(isinstance(item, ToolMessage) for item in messages[-2:]):
            marker = "model-final-inflight" if self.block_final else "model-final-retry"
            _append_audit(self.audit_path, marker)
            if self.block_final:
                await asyncio.Event().wait()
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="跨进程恢复完成。"))]
            )
        return ChatResult(generations=[ChatGeneration(message=self._message(messages))])

    def _message(self, messages: list[BaseMessage]) -> AIMessage:
        del messages
        _append_audit(self.audit_path, "model-tool-request")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": _TOOL_NAME,
                    "args": {"note": "受控恢复步骤"},
                    "id": "process-recovery-tool-call",
                    "type": "tool_call",
                }
            ],
        )


def _runtime_process(
    database_url: str,
    run_id: str,
    audit_path: str,
    *,
    block_final: bool,
    owner_id: str,
) -> None:
    asyncio.run(
        _run_runtime_process(
            database_url,
            run_id,
            audit_path,
            block_final=block_final,
            owner_id=owner_id,
        )
    )


async def _run_runtime_process(
    database_url: str,
    run_id: str,
    audit_path: str,
    *,
    block_final: bool,
    owner_id: str,
) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    control = RuntimeExecutionControlService(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        execution_repo_factory=SqlalchemyRuntimeExecutionRepository,
        lease_seconds=0.5,
    )

    @tool
    async def record_research_step(note: str) -> str:
        """记录测试用受控步骤；调用内容不写入审计文件。"""
        del note
        _append_audit(audit_path, "tool-confirmed")
        return "recorded"

    store = PostgresCheckpointStore(database_url)
    try:
        async with store.open() as saver:
            await saver.setup()
            runtime = DeepAgentsResearchAgentRuntime(
                model=_ProcessRecoveryModel(
                    audit_path=audit_path, block_final=block_final
                ),
                tools=(record_research_step,),
                checkpointer=saver,
                execution_control=control,
                runtime_owner_id=owner_id,
                lease_heartbeat_interval_seconds=0.1,
            )
            executor = AgentTurnExecutor(
                session_factory=factory,
                run_repo_factory=SqlalchemyRunRepository,
                agent_repo_factory=SqlalchemyAgentRepository,
                event_repo_factory=SqlalchemyEventRepository,
                evidence_repo_factory=SqlalchemyEvidenceRepository,
                claim_set_repo_factory=SqlalchemyClaimSetRepository,
                runtime=runtime,
                cancellation_poll_interval_seconds=0.05,
            )
            async with factory() as session:
                run = await SqlalchemyRunRepository(session).get_by_id(run_id)
            assert run is not None
            await executor.execute(run, f"process-recovery:{owner_id}")
    finally:
        await engine.dispose()


async def _wait_for_audit(path: Path, marker: str, timeout: float = 10) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.exists() and marker in path.read_text(encoding="utf-8").splitlines():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"未在期限内观察到 {marker}")


async def test_second_os_process_resumes_after_first_is_terminated(
    db_engine, tmp_path: Path
) -> None:
    """Tool Step 同步 checkpoint 后杀进程；新进程不重放已确认模型/Tool。"""
    scenario = await seed_agent_scenario(db_engine, owner_id="process-recovery-owner")
    now = datetime.now(UTC)
    async with scenario.factory() as session:
        agent_repo = SqlalchemyAgentRepository(session)
        agent_session = create_agent_session(
            owner_id=scenario.actor.owner_id,
            project_id=scenario.project.project_id,
            title="进程恢复",
        )
        await agent_repo.add_session(agent_session)
        run = create_run(
            scenario.project.project_id, scenario.actor.owner_id, RunType.AGENT_TURN
        ).transition_to(RunStatus.RUNNING)
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        sequence = await agent_repo.allocate_message_sequence(agent_session.session_id)
        message = create_agent_message(
            session_id=agent_session.session_id,
            last_sequence=sequence - 1,
            role=AgentMessageRole.USER,
            content="执行一次受控研究步骤",
            turn_run_id=run.run_id,
            idempotency_key="process-recovery-message",
        )
        context = create_context_snapshot(
            owner_id=run.owner_id,
            project_id=run.project_id,
            session_id=agent_session.session_id,
            turn_run_id=run.run_id,
            user_message_id=message.message_id,
            history_through_sequence=sequence,
            review_output_id=scenario.matrix.output_id,
        )
        policy = create_policy_snapshot(
            owner_id=run.owner_id,
            project_id=run.project_id,
            session_id=agent_session.session_id,
            turn_run_id=run.run_id,
            allowed_tool_names=(_TOOL_NAME,),
            max_model_calls=2,
            max_tool_calls=1,
        )
        turn = create_agent_turn_run(
            turn_run_id=run.run_id,
            session_id=agent_session.session_id,
            user_message_id=message.message_id,
            context_snapshot_id=context.snapshot_id,
            policy_snapshot_id=policy.snapshot_id,
        )
        await agent_repo.add_message(message)
        await session.flush()
        await agent_repo.add_context_snapshot(context)
        await agent_repo.add_policy_snapshot(policy)
        await session.flush()
        await agent_repo.add_turn(turn)
        await session.flush()
        assert await agent_repo.try_claim_active_turn(agent_session.session_id, run.run_id)
        await SqlalchemyAttemptRepository(session).add(
            RunAttempt(
                attempt_id="process-attempt-1",
                run_id=run.run_id,
                attempt_number=1,
                worker_id="worker-process-1",
                status=AttemptStatus.RUNNING,
                started_at=now,
                heartbeat_at=now,
            )
        )
        await session.commit()

    database_url = db_engine.url.render_as_string(hide_password=False)
    audit_path = tmp_path / "runtime-process-audit.log"
    process_context = multiprocessing.get_context("spawn")
    first = process_context.Process(
        target=_runtime_process,
        kwargs={
            "database_url": database_url,
            "run_id": run.run_id,
            "audit_path": str(audit_path),
            "block_final": True,
            "owner_id": "runtime-process-1",
        },
    )
    first.start()
    await _wait_for_audit(audit_path, "model-final-inflight")
    first.terminate()
    first.join(timeout=5)
    assert not first.is_alive()
    assert first.exitcode is not None and first.exitcode != 0

    await asyncio.sleep(0.7)
    retry_at = datetime.now(UTC)
    async with scenario.factory() as session:
        attempts = SqlalchemyAttemptRepository(session)
        assert await attempts.finish_if_running(
            "process-attempt-1", AttemptStatus.FAILED, retry_at
        )
        await attempts.add(
            RunAttempt(
                attempt_id="process-attempt-2",
                run_id=run.run_id,
                attempt_number=2,
                worker_id="worker-process-2",
                status=AttemptStatus.RUNNING,
                started_at=retry_at,
                heartbeat_at=retry_at,
            )
        )
        await session.commit()

    second = process_context.Process(
        target=_runtime_process,
        kwargs={
            "database_url": database_url,
            "run_id": run.run_id,
            "audit_path": str(audit_path),
            "block_final": False,
            "owner_id": "runtime-process-2",
        },
    )
    second.start()
    second.join(timeout=15)
    if second.is_alive():
        second.terminate()
        second.join(timeout=5)
        raise AssertionError("第二个 Runtime 进程未在期限内完成")
    assert second.exitcode == 0

    audit = audit_path.read_text(encoding="utf-8").splitlines()
    assert audit.count("model-tool-request") == 1
    assert audit.count("tool-confirmed") == 1
    assert audit.count("model-final-inflight") == 1
    assert audit.count("model-final-retry") == 1

    async with scenario.factory() as session:
        stored_run = await SqlalchemyRunRepository(session).get_by_id(run.run_id)
        execution = await SqlalchemyRuntimeExecutionRepository(session).get(run.run_id)
        messages = await SqlalchemyAgentRepository(session).list_messages_scoped(
            agent_session.session_id, run.owner_id
        )
    assert stored_run is not None and stored_run.status is RunStatus.SUCCEEDED
    assert execution is not None
    assert execution.state.value == "succeeded"
    assert execution.fencing_token == 2
    assert [item.role for item in messages] == [
        AgentMessageRole.USER,
        AgentMessageRole.ASSISTANT,
    ]
    assert messages[-1].content == "跨进程恢复完成。"
