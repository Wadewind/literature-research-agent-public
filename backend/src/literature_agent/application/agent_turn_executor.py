"""AgentTurn Worker 执行器：Runtime 事务外调用、结果短事务提交。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntime,
    RuntimeExecutionState,
    RuntimeTurnRequest,
)
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.research_agent import (
    AgentArtifactCandidate,
    AgentMessageRole,
    create_agent_artifact_candidate,
    create_agent_message,
    same_agent_artifact_candidate_fact,
)
from literature_agent.domain.run import Run, RunStatus

TSession = TypeVar("TSession", bound=Session)


class AgentTurnExecutor[TSession: Session]:
    """用业务快照调用 Runtime，再独立提交业务结果。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        runtime: ResearchAgentRuntime,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._agent_repo_factory = agent_repo_factory
        self._event_repo_factory = event_repo_factory
        self._runtime = runtime
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def execute(self, run: Run, correlation_id: str) -> None:
        """所有 Runtime I/O 均发生在读取事务结束以后。"""
        async with self._session_factory() as session:
            repo = self._agent_repo_factory(session)
            turn = await repo.get_turn_scoped(run.run_id, run.owner_id)
            if turn is None:
                raise ValueError("agent_turn_scope_invalid")
            message = await repo.get_message_by_run_and_role(
                run.run_id, AgentMessageRole.USER.value
            )
            context = await repo.get_context_snapshot(turn.context_snapshot_id)
            policy = await repo.get_policy_snapshot(turn.policy_snapshot_id)
            if message is None or context is None or policy is None:
                raise ValueError("agent_turn_input_missing")
            if (
                context.owner_id != run.owner_id
                or context.project_id != run.project_id
                or context.session_id != turn.session_id
                or context.user_message_id != message.message_id
                or policy.owner_id != run.owner_id
                or policy.project_id != run.project_id
                or policy.session_id != turn.session_id
            ):
                raise ValueError("agent_turn_scope_invalid")
            request = RuntimeTurnRequest(
                turn.session_id, run.run_id, message.message_id, message.content, context, policy
            )

        async for _event in self._runtime.execute_turn(request):
            pass
        reconciliation = await self._runtime.reconcile_turn(run.run_id)
        if reconciliation.state is not RuntimeExecutionState.SUCCEEDED:
            raise RuntimeError(f"Runtime 未成功完成: {reconciliation.state.value}")
        result = await self._runtime.collect_turn_result(run.run_id)
        requested_candidates: list[AgentArtifactCandidate] = []
        for candidate in result.artifact_candidates:
            requested = create_agent_artifact_candidate(
                candidate_id=candidate.candidate_id,
                owner_id=run.owner_id,
                project_id=run.project_id,
                session_id=turn.session_id,
                turn_run_id=run.run_id,
                name=candidate.name,
                media_type=candidate.media_type,
                content_ref=candidate.content_ref,
                content_hash=candidate.content_hash,
                size_bytes=candidate.size_bytes,
            )
            if any(
                same_agent_artifact_candidate_fact(existing, requested)
                for existing in requested_candidates
            ):
                continue
            requested_candidates.append(requested)

        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            locked = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if locked is None or locked.status is not RunStatus.RUNNING:
                await session.commit()
                return
            repo = self._agent_repo_factory(session)
            locked_turn = await repo.get_turn_scoped(run.run_id, run.owner_id)
            locked_session = await repo.get_session_scoped_for_update(turn.session_id, run.owner_id)
            if (
                locked_turn != turn
                or locked_session is None
                or locked_session.project_id != run.project_id
                or locked_session.active_turn_run_id != run.run_id
            ):
                raise RunConcurrentModificationError(run.run_id)
            session_binding = await repo.get_or_add_session_binding(reconciliation.session_binding)
            if (
                session_binding != reconciliation.session_binding
                or reconciliation.turn_binding.session_binding_id != session_binding.binding_id
            ):
                raise RunConcurrentModificationError(run.run_id)
            turn_binding = await repo.get_or_add_turn_binding(reconciliation.turn_binding)
            if turn_binding != reconciliation.turn_binding:
                raise RunConcurrentModificationError(run.run_id)

            sequence = await repo.allocate_message_sequence(turn.session_id)
            assistant = create_agent_message(
                session_id=turn.session_id,
                last_sequence=sequence - 1,
                role=AgentMessageRole.ASSISTANT,
                content=result.assistant_content,
                turn_run_id=run.run_id,
                idempotency_key=f"assistant:{run.run_id}",
            )
            await repo.add_message(assistant)
            staged: list[AgentArtifactCandidate] = []
            for candidate in requested_candidates:
                saved = await repo.get_or_add_candidate(candidate)
                if not same_agent_artifact_candidate_fact(saved, candidate):
                    raise RunConcurrentModificationError(run.run_id)
                staged.append(saved)

            event_repo = self._event_repo_factory(session)
            event_sequence = locked.event_sequence
            await event_repo.add(
                create_event(
                    run.run_id,
                    event_sequence,
                    "agent_runtime_bound",
                    "system",
                    correlation_id,
                    {
                        "binding_id": session_binding.binding_id,
                        "generation": session_binding.generation,
                    },
                )
            )
            event_sequence += 1
            for candidate in staged:
                await event_repo.add(
                    create_event(
                        run.run_id,
                        event_sequence,
                        "agent_artifact_staged",
                        "system",
                        correlation_id,
                        {
                            "candidate_id": candidate.candidate_id,
                            "content_hash": candidate.content_hash,
                            "size_bytes": candidate.size_bytes,
                            "media_type": candidate.media_type,
                        },
                    )
                )
                event_sequence += 1
            await event_repo.add(
                create_event(
                    run.run_id,
                    event_sequence,
                    "agent_turn_succeeded",
                    "system",
                    correlation_id,
                    {
                        "assistant_message_id": assistant.message_id,
                        "candidate_count": len(staged),
                        "evidence_count": 0,
                    },
                )
            )
            event_sequence += 1
            if not await run_repo.update_status(
                run.run_id, RunStatus.RUNNING, RunStatus.SUCCEEDED, event_sequence
            ):
                raise RunConcurrentModificationError(run.run_id)
            if not await repo.release_active_turn(turn.session_id, run.run_id):
                raise RunConcurrentModificationError(run.run_id)
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
