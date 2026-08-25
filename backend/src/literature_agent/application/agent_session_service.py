"""Project-scoped AgentSession 与逐轮消息用例。"""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRepository,
)
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.project_paper_repository import ProjectPaperRepository
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    AgentReviewOutputNotFoundError,
    AgentSessionBusyError,
    AgentSessionNotFoundError,
    AgentTurnNotFoundError,
    IdempotencyConflictError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ProjectNotIndexedError,
)
from literature_agent.domain.queue_outbox import create_outbox_entry
from literature_agent.domain.research_agent import (
    AgentArtifactCandidate,
    AgentMessage,
    AgentMessageRole,
    AgentSession,
    AgentTurnRun,
    ContextSnapshot,
    PolicySnapshot,
    ProjectIndexContextRef,
    create_agent_message,
    create_agent_session,
    create_agent_turn_run,
    create_context_snapshot,
    create_policy_snapshot,
)
from literature_agent.domain.review import ReviewOutputType
from literature_agent.domain.run import Run, RunStatus, RunType, create_run

TSession = TypeVar("TSession", bound=Session)


@dataclass(frozen=True, slots=True)
class PostAgentMessageResult:
    user_message_id: str
    run_id: str
    status: str


@dataclass(frozen=True, slots=True)
class AgentTurnView:
    run: Run
    turn: AgentTurnRun
    context_snapshot: ContextSnapshot
    policy_snapshot: PolicySnapshot
    candidates: tuple[AgentArtifactCandidate, ...]


class AgentSessionService[TSession: Session]:
    """负责平台授权、快照固化和原子 Turn 提交。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        project_paper_repo_factory: Callable[[TSession], ProjectPaperRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        idempotency_repo_factory: Callable[[TSession], IdempotencyRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._agent_repo_factory = agent_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._project_paper_repo_factory = project_paper_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._review_repo_factory = review_repo_factory
        self._idempotency_repo_factory = idempotency_repo_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def create_session(
        self, actor: ActorContext, project_id: str, *, title: str | None
    ) -> AgentSession:
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            if project.is_archived:
                raise ProjectArchivedError(project_id)
            value = create_agent_session(
                owner_id=actor.owner_id, project_id=project_id, title=title
            )
            await self._agent_repo_factory(session).add_session(value)
            await session.commit()
            return value

    async def get_session(self, actor: ActorContext, session_id: str) -> AgentSession:
        async with self._session_factory() as session:
            value = await self._agent_repo_factory(session).get_session_scoped(
                session_id, actor.owner_id
            )
            if value is None:
                raise AgentSessionNotFoundError(session_id)
            return value

    async def list_messages(self, actor: ActorContext, session_id: str) -> list[AgentMessage]:
        async with self._session_factory() as session:
            repo = self._agent_repo_factory(session)
            if await repo.get_session_scoped(session_id, actor.owner_id) is None:
                raise AgentSessionNotFoundError(session_id)
            return await repo.list_messages_scoped(session_id, actor.owner_id)

    async def post_message(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        content: str,
        review_output_id: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> PostAgentMessageResult:
        if not idempotency_key.strip() or len(idempotency_key) > 255:
            raise ValueError("Idempotency-Key 不能为空且长度不得超过 255")
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "session_id": session_id,
                    "content": content,
                    "review_output_id": review_output_id,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        async with self._session_factory() as session:
            idem = self._idempotency_repo_factory(session)
            existing = await idem.get(actor.owner_id, idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError(idempotency_key)
                assert existing.run_id is not None
                message = await self._agent_repo_factory(session).get_message_by_run_and_role(
                    existing.run_id, AgentMessageRole.USER.value
                )
                if message is None:
                    raise AgentSessionNotFoundError(session_id)
                return PostAgentMessageResult(message.message_id, existing.run_id, existing.status)

            agent_repo = self._agent_repo_factory(session)
            agent_session = await agent_repo.get_session_scoped_for_update(
                session_id, actor.owner_id
            )
            if agent_session is None:
                raise AgentSessionNotFoundError(session_id)
            project = await self._project_repo_factory(session).get_by_id(agent_session.project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(agent_session.project_id)
            if project.is_archived:
                raise ProjectArchivedError(project.project_id)
            if agent_session.active_turn_run_id is not None:
                active = await self._run_repo_factory(session).get_by_id(
                    agent_session.active_turn_run_id
                )
                if active is not None and active.status not in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    raise AgentSessionBusyError(session_id)
                await agent_repo.release_active_turn(session_id, agent_session.active_turn_run_id)

            output = await self._review_repo_factory(session).get_output_scoped(
                review_output_id, project.project_id, actor.owner_id
            )
            if (
                output is None
                or output.output_type is not ReviewOutputType.EVIDENCE_MATRIX
                or output.output_key != "evidence-matrix"
            ):
                raise AgentReviewOutputNotFoundError(review_output_id)

            refs: list[ProjectIndexContextRef] = []
            for relation in await self._project_paper_repo_factory(session).list_by_project(
                project.project_id
            ):
                paper = await self._paper_repo_factory(session).get_by_id(relation.paper_id)
                if paper is None or paper.owner_id != actor.owner_id or paper.is_archived:
                    continue
                chunk_set = await self._chunk_set_repo_factory(session).get_ready_by_version(
                    relation.selected_version_id
                )
                if chunk_set is not None:
                    refs.append(
                        ProjectIndexContextRef(
                            relation.paper_id, relation.selected_version_id, chunk_set.chunk_set_id
                        )
                    )
            if not refs:
                raise ProjectNotIndexedError(project.project_id)

            run = create_run(project.project_id, actor.owner_id, RunType.AGENT_TURN, {})
            sequence = await agent_repo.allocate_message_sequence(session_id)
            user_message = create_agent_message(
                session_id=session_id,
                last_sequence=sequence - 1,
                role=AgentMessageRole.USER,
                content=content,
                turn_run_id=run.run_id,
                idempotency_key=idempotency_key,
            )
            context = create_context_snapshot(
                owner_id=actor.owner_id,
                project_id=project.project_id,
                session_id=session_id,
                turn_run_id=run.run_id,
                user_message_id=user_message.message_id,
                history_through_sequence=sequence,
                project_index_refs=tuple(refs),
                review_output_id=review_output_id,
            )
            policy = create_policy_snapshot(
                owner_id=actor.owner_id,
                project_id=project.project_id,
                session_id=session_id,
                turn_run_id=run.run_id,
                max_model_calls=1,
                max_tool_calls=0,
            )
            turn = create_agent_turn_run(
                turn_run_id=run.run_id,
                session_id=session_id,
                user_message_id=user_message.message_id,
                context_snapshot_id=context.snapshot_id,
                policy_snapshot_id=policy.snapshot_id,
            )
            run = replace(
                run,
                input_payload={
                    "session_id": session_id,
                    "user_message_id": user_message.message_id,
                    "context_snapshot_id": context.snapshot_id,
                    "policy_snapshot_id": policy.snapshot_id,
                },
                event_sequence=3,
            )
            run_repo = self._run_repo_factory(session)
            await run_repo.add(run)
            await session.flush()
            await agent_repo.add_message(user_message)
            await session.flush()
            await agent_repo.add_context_snapshot(context)
            await agent_repo.add_policy_snapshot(policy)
            await session.flush()
            await agent_repo.add_turn(turn)
            await session.flush()
            if not await agent_repo.try_claim_active_turn(session_id, run.run_id):
                raise AgentSessionBusyError(session_id)
            event_repo = self._event_repo_factory(session)
            await event_repo.add(
                create_event(
                    run.run_id, 1, "run_created", "user", correlation_id, {"status": "queued"}
                )
            )
            await event_repo.add(
                create_event(
                    run.run_id,
                    2,
                    "agent_message_accepted",
                    "user",
                    correlation_id,
                    {
                        "session_id": session_id,
                        "message_id": user_message.message_id,
                        "review_output_id": review_output_id,
                        "project_index_count": len(refs),
                    },
                )
            )
            await self._outbox_repo_factory(session).add(create_outbox_entry(run.run_id))
            await idem.add(
                IdempotencyRecord(
                    actor.owner_id,
                    idempotency_key,
                    project.project_id,
                    request_hash,
                    run.run_id,
                    status="queued",
                )
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return PostAgentMessageResult(user_message.message_id, run.run_id, "queued")

    async def get_turn(self, actor: ActorContext, run_id: str) -> AgentTurnView:
        async with self._session_factory() as session:
            repo = self._agent_repo_factory(session)
            turn = await repo.get_turn_scoped(run_id, actor.owner_id)
            run = await self._run_repo_factory(session).get_by_id(run_id)
            if turn is None or run is None or run.owner_id != actor.owner_id:
                raise AgentTurnNotFoundError(run_id)
            context = await repo.get_context_snapshot(turn.context_snapshot_id)
            policy = await repo.get_policy_snapshot(turn.policy_snapshot_id)
            assert context is not None and policy is not None
            candidates = await repo.list_candidates_scoped(run_id, actor.owner_id)
            return AgentTurnView(run, turn, context, policy, tuple(candidates))
