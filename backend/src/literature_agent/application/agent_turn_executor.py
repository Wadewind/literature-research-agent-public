"""AgentTurn Worker 执行器：Runtime 事务外调用、结果短事务提交。"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.agent_artifact_publisher import (
    AgentArtifactPublisher,
)
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.claim_set_repository import ClaimSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.project_research_context import (
    READ_REVIEW_EVIDENCE_MATRIX,
    SEARCH_PROJECT_CHUNKS,
)
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntime,
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeExecutionState,
    RuntimeResumeRequest,
    RuntimeTurnReconciliation,
    RuntimeTurnRequest,
)
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.workspace_snapshot_publisher import (
    WorkspaceSnapshotPublisher,
)
from literature_agent.domain.agent_answer import (
    INSUFFICIENT_AGENT_EVIDENCE_TEXT,
    parse_agent_answer,
)
from literature_agent.domain.answer_schema import RagAnswerOutput
from literature_agent.domain.citation_validator import validate_citations
from literature_agent.domain.event import create_event
from literature_agent.domain.evidence import (
    Citation,
    create_claim,
    create_claim_set,
)
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
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        claim_set_repo_factory: Callable[[TSession], ClaimSetRepository],
        runtime: ResearchAgentRuntime,
        event_notifier: EventNotifier | None = None,
        cancellation_poll_interval_seconds: float = 0.5,
        workspace_snapshot_publisher_factory: Callable[
            [TSession], WorkspaceSnapshotPublisher
        ]
        | None = None,
        workspace_snapshot_required: bool = False,
        agent_artifact_publisher_factory: Callable[[TSession], AgentArtifactPublisher]
        | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._agent_repo_factory = agent_repo_factory
        self._event_repo_factory = event_repo_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._claim_set_repo_factory = claim_set_repo_factory
        self._runtime = runtime
        self._event_notifier = event_notifier or NoopEventNotifier()
        self._cancellation_poll_interval_seconds = cancellation_poll_interval_seconds
        self._workspace_snapshot_publisher_factory = (
            workspace_snapshot_publisher_factory
        )
        self._workspace_snapshot_required = workspace_snapshot_required
        self._agent_artifact_publisher_factory = agent_artifact_publisher_factory
        if workspace_snapshot_required and workspace_snapshot_publisher_factory is None:
            raise ValueError("Deep Runtime 必须配置 WorkspaceSnapshot publisher")

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

        current_status = await self._get_run_status(run.run_id, run.owner_id)
        if current_status is RunStatus.CANCEL_REQUESTED:
            await self._commit_cancellation(run, turn.session_id, correlation_id)
            return
        if current_status is not RunStatus.RUNNING:
            return

        reconciliation = await self._reconcile_or_execute(
            request, run, turn.session_id, correlation_id
        )
        if reconciliation is None:
            return
        result = await self._runtime.collect_turn_result(run.run_id)
        if result.turn_run_id != run.run_id:
            self._raise_runtime_scope_mismatch()
        citation_contract_enabled = bool(
            {SEARCH_PROJECT_CHUNKS, READ_REVIEW_EVIDENCE_MATRIX}
            & set(request.policy_snapshot.allowed_tool_names)
            or result.evidence_ids
            or result.assistant_content.strip() == INSUFFICIENT_AGENT_EVIDENCE_TEXT
        )
        output = (
            self._parse_runtime_answer(result.assistant_content, result.evidence_ids)
            if citation_contract_enabled
            else None
        )
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
            if locked is not None and locked.status is RunStatus.CANCEL_REQUESTED:
                await self._commit_cancellation_in_session(
                    session,
                    locked,
                    turn.session_id,
                    correlation_id,
                )
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return
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
            claim_set = None
            claims = []
            citations = []
            if output is not None:
                evidence = await self._evidence_repo_factory(session).list_by_ids(
                    list(result.evidence_ids)
                )
                validation = validate_citations(output, evidence=evidence, run_id=run.run_id)
                if (
                    not validation.passed
                    or len(evidence) != len(set(result.evidence_ids))
                    or any(item.project_id != locked.project_id for item in evidence)
                ):
                    raise ResearchAgentRuntimeError(
                        kind=RuntimeErrorKind.PERMANENT,
                        code="runtime_citation_invalid",
                        safe_message="Agent 最终回答的 Evidence 引用校验失败",
                    )
                claim_set = create_claim_set(run.run_id, output.answer_status)
                claims = [
                    create_claim(claim_set.claim_set_id, index, draft.text)
                    for index, draft in enumerate(output.claims, start=1)
                ]
                citations = [
                    Citation(claim.claim_id, evidence_id)
                    for claim, draft in zip(claims, output.claims, strict=True)
                    for evidence_id in draft.evidence_ids
                ]
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
                claim_set_id=claim_set.claim_set_id if claim_set is not None else None,
            )
            if claim_set is not None:
                claim_repo = self._claim_set_repo_factory(session)
                await claim_repo.add_claim_set(claim_set)
                await session.flush()
                await claim_repo.add_claims(claims)
                await session.flush()
                await claim_repo.add_citations(citations)
            await repo.add_message(assistant)
            staged: list[AgentArtifactCandidate] = []
            for candidate in requested_candidates:
                saved = await repo.get_or_add_candidate(candidate)
                if not same_agent_artifact_candidate_fact(saved, candidate):
                    raise RunConcurrentModificationError(run.run_id)
                staged.append(saved)

            artifacts = (
                await self._agent_artifact_publisher_factory(session).publish_for_success(
                    owner_id=run.owner_id,
                    project_id=run.project_id,
                    session_id=turn.session_id,
                    turn_run_id=run.run_id,
                )
                if self._agent_artifact_publisher_factory is not None
                else ()
            )

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
            for artifact in artifacts:
                await event_repo.add(
                    create_event(
                        run.run_id,
                        event_sequence,
                        "agent_artifact_committed",
                        "system",
                        correlation_id,
                        {
                            "artifact_id": artifact.artifact_id,
                            "candidate_id": artifact.candidate_id,
                            "content_hash": artifact.content_hash,
                            "size_bytes": artifact.size_bytes,
                            "media_type": artifact.media_type,
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
                        "artifact_count": len(artifacts),
                        "evidence_count": len(result.evidence_ids),
                    },
                )
            )
            event_sequence += 1
            if not await run_repo.update_status(
                run.run_id, RunStatus.RUNNING, RunStatus.SUCCEEDED, event_sequence
            ):
                raise RunConcurrentModificationError(run.run_id)
            if self._workspace_snapshot_publisher_factory is not None:
                published = await self._workspace_snapshot_publisher_factory(
                    session
                ).publish_for_success(
                    owner_id=run.owner_id,
                    project_id=run.project_id,
                    session_id=turn.session_id,
                    turn_run_id=run.run_id,
                    required=self._workspace_snapshot_required,
                )
                if not published:
                    raise RunConcurrentModificationError(run.run_id)
            if not await repo.release_active_turn(turn.session_id, run.run_id):
                raise RunConcurrentModificationError(run.run_id)
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)

    @staticmethod
    def _parse_runtime_answer(
        assistant_content: str, runtime_evidence_ids: tuple[str, ...]
    ) -> RagAnswerOutput:
        """正文标记与 Runtime 结果必须精确一致。"""
        try:
            output, marked_ids = parse_agent_answer(assistant_content)
        except ValueError as exc:
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.PERMANENT,
                code="runtime_output_invalid",
                safe_message="Agent 最终回答格式非法",
            ) from exc
        if marked_ids != runtime_evidence_ids:
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.PERMANENT,
                code="runtime_evidence_mismatch",
                safe_message="Agent 最终回答正文与 Runtime Evidence 不一致",
            )
        return output

    async def _reconcile_or_execute(
        self,
        request: RuntimeTurnRequest,
        run: Run,
        session_id: str,
        correlation_id: str,
    ) -> RuntimeTurnReconciliation | None:
        """先对账稳定 Turn；只有 Runtime 明确未知时才追加首次输入。"""
        try:
            reconciliation = await self._runtime.reconcile_turn(request.turn_run_id)
        except ResearchAgentRuntimeError as exc:
            if exc.code != "runtime_turn_not_found":
                raise
            current_status = await self._get_run_status(run.run_id, run.owner_id)
            if current_status is RunStatus.CANCEL_REQUESTED:
                await self._commit_cancellation(run, session_id, correlation_id)
                return None
            if current_status is not RunStatus.RUNNING:
                return None
            cancelled = await self._execute_with_cancellation_watch(
                request, run, session_id, correlation_id
            )
            if cancelled:
                return None
            reconciliation = await self._runtime.reconcile_turn(request.turn_run_id)

        self._validate_reconciliation_scope(request, reconciliation)
        if (
            reconciliation.state is RuntimeExecutionState.SUCCEEDED
            and reconciliation.result_available
        ):
            return reconciliation
        if (
            reconciliation.state is RuntimeExecutionState.RUNNING
            and reconciliation.resume_available
        ):
            cancelled = await self._execute_with_cancellation_watch(
                request,
                run,
                session_id,
                correlation_id,
                resume=True,
            )
            if cancelled:
                return None
            reconciliation = await self._runtime.reconcile_turn(request.turn_run_id)
            self._validate_reconciliation_scope(request, reconciliation)
            if (
                reconciliation.state is RuntimeExecutionState.SUCCEEDED
                and reconciliation.result_available
            ):
                return reconciliation
        if reconciliation.state is RuntimeExecutionState.RUNNING:
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.TEMPORARY,
                code="runtime_turn_still_running",
                safe_message="Runtime Turn 仍在执行，稍后继续对账",
            )
        if reconciliation.state is RuntimeExecutionState.INTERRUPTED:
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.TEMPORARY,
                code="runtime_turn_interrupted",
                safe_message="Runtime Turn 等待受控恢复输入",
            )
        if reconciliation.state is RuntimeExecutionState.CANCELLED:
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.CANCELLED,
                code="runtime_turn_cancelled",
                safe_message="Runtime Turn 已取消",
            )
        if reconciliation.state is RuntimeExecutionState.FAILED:
            raise ResearchAgentRuntimeError(
                kind=RuntimeErrorKind.PERMANENT,
                code="runtime_turn_failed",
                safe_message="Runtime Turn 已永久失败",
            )
        raise ResearchAgentRuntimeError(
            kind=RuntimeErrorKind.TEMPORARY,
            code="runtime_result_not_ready",
            safe_message="Runtime 成功结果尚不可收集",
        )

    async def _execute_with_cancellation_watch(
        self,
        request: RuntimeTurnRequest,
        run: Run,
        session_id: str,
        correlation_id: str,
        *,
        resume: bool = False,
    ) -> bool:
        """并行观察业务取消意图，并在事务外传播到 Runtime。"""

        async def consume() -> None:
            stream = (
                self._runtime.resume_turn(
                    RuntimeResumeRequest(
                        turn_run_id=request.turn_run_id,
                        response=None,
                        turn_request=request,
                    )
                )
                if resume
                else self._runtime.execute_turn(request)
            )
            async for _event in stream:
                pass

        stream_task = asyncio.create_task(consume())
        status_task = asyncio.create_task(
            self._wait_until_run_leaves_running(run.run_id, run.owner_id)
        )
        try:
            done, _pending = await asyncio.wait(
                {stream_task, status_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if status_task in done:
                status = status_task.result()
                if status is not RunStatus.CANCEL_REQUESTED:
                    stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await stream_task
                    return True
                await self._runtime.cancel_turn(run.run_id)
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task
                await self._commit_cancellation(run, session_id, correlation_id)
                return True

            status_task.cancel()
            with suppress(asyncio.CancelledError):
                await status_task
            await stream_task
            return False
        finally:
            for task in (stream_task, status_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stream_task, status_task, return_exceptions=True)

    @staticmethod
    def _validate_reconciliation_scope(
        request: RuntimeTurnRequest,
        reconciliation: RuntimeTurnReconciliation,
    ) -> None:
        """拒绝 Runtime 返回的跨 Session/Turn Binding。"""
        session_binding = reconciliation.session_binding
        turn_binding = reconciliation.turn_binding
        if (
            reconciliation.turn_run_id != request.turn_run_id
            or session_binding.session_id != request.session_id
            or turn_binding.session_id != request.session_id
            or turn_binding.turn_run_id != request.turn_run_id
            or turn_binding.session_binding_id != session_binding.binding_id
        ):
            AgentTurnExecutor._raise_runtime_scope_mismatch()

    @staticmethod
    def _raise_runtime_scope_mismatch() -> None:
        """抛出不携带 Runtime 原始输出的永久作用域错误。"""
        raise ResearchAgentRuntimeError(
            kind=RuntimeErrorKind.PERMANENT,
            code="runtime_scope_mismatch",
            safe_message="Runtime Turn 作用域校验失败",
        )

    async def _wait_until_run_leaves_running(
        self, run_id: str, owner_id: str
    ) -> RunStatus | None:
        """用独立短读事务轮询协作取消；不在事务中调用 Runtime。"""
        while True:
            status = await self._get_run_status(run_id, owner_id)
            if status is not RunStatus.RUNNING:
                return status
            await asyncio.sleep(self._cancellation_poll_interval_seconds)

    async def _get_run_status(self, run_id: str, owner_id: str) -> RunStatus | None:
        async with self._session_factory() as session:
            value = await self._run_repo_factory(session).get_by_id(run_id)
            if value is None or value.owner_id != owner_id:
                return None
            return value.status

    async def _commit_cancellation(
        self, run: Run, session_id: str, correlation_id: str
    ) -> None:
        """幂等提交业务取消与 Session 释放，不提交 Runtime 结果。"""
        changed = False
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            locked = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if locked is not None and locked.status is RunStatus.CANCEL_REQUESTED:
                await self._commit_cancellation_in_session(
                    session, locked, session_id, correlation_id
                )
                changed = True
            elif locked is not None and locked.status is RunStatus.CANCELLED:
                await self._agent_repo_factory(session).release_active_turn(
                    session_id, run.run_id
                )
            await session.commit()
        if changed:
            await notify_run_event(self._event_notifier, run.run_id)

    async def _commit_cancellation_in_session(
        self,
        session: TSession,
        locked: Run,
        session_id: str,
        correlation_id: str,
    ) -> None:
        """在已持 Run 行锁的事务中原子收敛取消、Event 与活动 Turn。"""
        locked.transition_to(RunStatus.CANCELLED)
        event_repo = self._event_repo_factory(session)
        await event_repo.add(
            create_event(
                locked.run_id,
                locked.event_sequence,
                "run_cancelled",
                "system",
                correlation_id,
                {},
            )
        )
        await event_repo.add(
            create_event(
                locked.run_id,
                locked.event_sequence + 1,
                "agent_turn_cancelled",
                "system",
                correlation_id,
                {"session_id": session_id},
            )
        )
        if not await self._run_repo_factory(session).update_status(
            locked.run_id,
            RunStatus.CANCEL_REQUESTED,
            RunStatus.CANCELLED,
            locked.event_sequence + 2,
        ):
            raise RunConcurrentModificationError(locked.run_id)
        if not await self._agent_repo_factory(session).release_active_turn(
            session_id, locked.run_id
        ):
            current = await self._agent_repo_factory(session).get_session_scoped_for_update(
                session_id, locked.owner_id
            )
            if current is None or current.active_turn_run_id is not None:
                raise RunConcurrentModificationError(locked.run_id)
