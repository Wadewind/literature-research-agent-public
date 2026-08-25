"""真实 PostgreSQL 下的两轮离线 Agent 业务闭环。"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.agent_session_service import AgentSessionService
from literature_agent.application.agent_turn_executor import AgentTurnExecutor
from literature_agent.application.ports.research_agent_runtime import RuntimeArtifactCandidate
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import ExecutionOutcome, RunExecutionService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.exceptions import (
    AgentReviewOutputNotFoundError,
    AgentSessionBusyError,
    AgentSessionNotFoundError,
    RunConcurrentModificationError,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.research_agent import RuntimeSessionBinding
from literature_agent.domain.review import ReviewOutputType, create_review_output, create_review_run
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.agent.fake_research_agent_runtime import (
    FakeResearchAgentRuntime,
)
from literature_agent.infrastructure.persistence.agent_repository import SqlalchemyAgentRepository
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import SqlalchemyEventRepository
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.models import ArtifactORM
from literature_agent.infrastructure.persistence.outbox_repository import SqlalchemyOutboxRepository
from literature_agent.infrastructure.persistence.paper_repository import SqlalchemyPaperRepository
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.parse_revision_repository import (
    SqlalchemyParseRevisionRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import SqlalchemyReviewRepository
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository


class _TrackedSessionFactory:
    """记录当前打开的应用事务，证明 Runtime 调用位于事务外。"""

    def __init__(self, factory) -> None:
        self.factory = factory
        self.active = 0

    @asynccontextmanager
    async def __call__(self):
        async with self.factory() as session:
            self.active += 1
            try:
                yield session
            finally:
                self.active -= 1


class _TransactionAssertingRuntime(FakeResearchAgentRuntime):
    def __init__(self, tracked: _TrackedSessionFactory) -> None:
        super().__init__()
        self.tracked = tracked
        self.invalid_candidate = False

    def execute_turn(self, request):
        assert self.tracked.active == 0
        return super().execute_turn(request)

    async def reconcile_turn(self, turn_run_id):
        assert self.tracked.active == 0
        return await super().reconcile_turn(turn_run_id)

    async def collect_turn_result(self, turn_run_id):
        assert self.tracked.active == 0
        result = await super().collect_turn_result(turn_run_id)
        if not self.invalid_candidate:
            return result
        return replace(
            result,
            artifact_candidates=(
                RuntimeArtifactCandidate(
                    candidate_id="",
                    name="",
                    media_type="",
                    content_ref="",
                    content_hash="not-sha256",
                    size_bytes=-1,
                ),
            ),
        )


@pytest.mark.asyncio
async def test_two_turns_reuse_runtime_session_and_persist_staged_candidates(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    actor = ActorContext(owner_id="agent-owner")
    async with factory() as session:
        project = create_project(owner_id=actor.owner_id, name="Agent 项目", description="")
        await SqlalchemyProjectRepository(session).add(project)
        paper = create_paper(actor.owner_id)
        await SqlalchemyPaperRepository(session).add(paper)
        await session.flush()
        version = create_paper_version(
            paper.paper_id, actor.owner_id, "a" * 64, "papers/a.pdf", 10, "application/pdf"
        )
        await SqlalchemyPaperVersionRepository(session).add(version)
        await session.flush()
        await SqlalchemyProjectPaperRepository(session).add(
            create_project_paper(project.project_id, paper.paper_id, version.version_id)
        )
        revision = create_parse_revision(
            version.version_id, "fake", "1.0", "b" * 64
        ).mark_succeeded(datetime.now(UTC))
        await SqlalchemyParseRevisionRepository(session).add(revision)
        await session.flush()
        chunk_set = create_chunk_set(revision.revision_id, "c" * 64).mark_ready(datetime.now(UTC))
        await SqlalchemyChunkSetRepository(session).add(chunk_set)
        review_run = create_run(project.project_id, actor.owner_id, RunType.REVIEW)
        await SqlalchemyRunRepository(session).add(review_run)
        await session.flush()
        review = create_review_run(
            run_id=review_run.run_id,
            research_question="研究问题",
            workflow_version="review.v1",
            model_profile_version="model.v1",
            prompt_versions={"matrix": "matrix.v1"},
            config_snapshot={},
        )
        review_repo = SqlalchemyReviewRepository(session)
        await review_repo.add_review_run(review)
        await session.flush()
        matrix = create_review_output(
            review_run_id=review_run.run_id,
            output_type=ReviewOutputType.EVIDENCE_MATRIX,
            output_key="evidence-matrix",
            version=1,
            schema_version="evidence-matrix.v1",
            payload={"rows": []},
            idempotency_key="matrix-1",
        )
        await review_repo.add_output(matrix)
        await session.commit()

    tracked_factory = _TrackedSessionFactory(factory)
    service = AgentSessionService(
        session_factory=tracked_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
    )
    runtime = _TransactionAssertingRuntime(tracked_factory)
    agent_executor = AgentTurnExecutor(
        session_factory=tracked_factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        runtime=runtime,
    )
    dispatcher = RunDispatcher(
        tracked_factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        {RunType.AGENT_TURN: agent_executor.execute},
    )
    runner = RunExecutionService(
        tracked_factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
        SqlalchemyAttemptRepository,
        SqlalchemyOutboxRepository,
        dispatcher.execute,
        "test-worker",
        heartbeat_interval_seconds=3600,
    )

    agent_session = await service.create_session(actor, project.project_id, title="研究会话")
    first = await service.post_message(
        actor,
        agent_session.session_id,
        content="分析第一轮",
        review_output_id=matrix.output_id,
        idempotency_key="agent-turn-1",
        correlation_id="c1",
    )
    replay = await service.post_message(
        actor,
        agent_session.session_id,
        content="分析第一轮",
        review_output_id=matrix.output_id,
        idempotency_key="agent-turn-1",
        correlation_id="c1-replay",
    )
    assert replay.run_id == first.run_id
    with pytest.raises(AgentSessionBusyError):
        await service.post_message(
            actor,
            agent_session.session_id,
            content="并发消息",
            review_output_id=matrix.output_id,
            idempotency_key="agent-turn-busy",
            correlation_id="busy",
        )
    assert len(await service.list_messages(actor, agent_session.session_id)) == 1
    async with factory() as session:
        queued = await SqlalchemyRunRepository(session).get_by_id(first.run_id)
        initial_events = await SqlalchemyEventRepository(session).list_by_run(first.run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(first.run_id)
        assert queued is not None and queued.status.value == "queued"
        assert [event.event_type for event in initial_events] == [
            "run_created",
            "agent_message_accepted",
        ]
        assert outbox is not None
    first_outcome = await runner.execute(first.run_id, "worker-1")
    if first_outcome is not ExecutionOutcome.COMPLETED:
        async with factory() as debugging_session:
            attempt = await SqlalchemyAttemptRepository(debugging_session).get_latest_by_run(
                first.run_id
            )
        pytest.fail(f"首轮执行失败: {attempt.error if attempt else 'missing attempt'}")
    with pytest.raises(AgentReviewOutputNotFoundError):
        await service.post_message(
            actor,
            agent_session.session_id,
            content="越权 Matrix",
            review_output_id="00000000-0000-0000-0000-000000000000",
            idempotency_key="agent-turn-wrong-matrix",
            correlation_id="wrong-matrix",
        )
    second = await service.post_message(
        actor,
        agent_session.session_id,
        content="继续第二轮",
        review_output_id=matrix.output_id,
        idempotency_key="agent-turn-2",
        correlation_id="c2",
    )
    assert await runner.execute(second.run_id, "worker-2") is ExecutionOutcome.COMPLETED

    messages = await service.list_messages(actor, agent_session.session_id)
    assert [item.sequence for item in messages] == [1, 2, 3, 4]
    assert [item.role.value for item in messages] == ["user", "assistant", "user", "assistant"]
    first_view = await service.get_turn(actor, first.run_id)
    second_view = await service.get_turn(actor, second.run_id)
    assert first_view.context_snapshot.review_output_id == matrix.output_id
    assert first_view.context_snapshot.project_index_refs[0].chunk_set_id == chunk_set.chunk_set_id
    assert first_view.candidates[0].status.value == "staged"
    assert second_view.candidates[0].status.value == "staged"
    with pytest.raises(AgentSessionNotFoundError):
        await service.get_session(ActorContext(owner_id="other-owner"), agent_session.session_id)
    async with factory() as session:
        repo = SqlalchemyAgentRepository(session)
        binding = await repo.get_session_binding(agent_session.session_id)
        first_binding = await repo.get_turn_binding(first.run_id)
        second_binding = await repo.get_turn_binding(second.run_id)
        assert binding is not None and first_binding is not None and second_binding is not None
        assert (
            first_binding.session_binding_id
            == second_binding.session_binding_id
            == binding.binding_id
        )
        assert first_binding.runtime_execution_id != second_binding.runtime_execution_id
        for run_id in (first.run_id, second.run_id):
            attempts = await SqlalchemyAttemptRepository(session).list_by_run(run_id)
            events = await SqlalchemyEventRepository(session).list_by_run(run_id)
            assert len(attempts) == 1
            assert attempts[0].status.value == "succeeded"
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
            assert all("分析第一轮" not in str(event.payload) for event in events)
        assert await session.get(ArtifactORM, first_view.candidates[0].candidate_id) is None

    async with factory() as session:
        repo = SqlalchemyAgentRepository(session)
        first_binding = await repo.get_session_binding(agent_session.session_id)
        assert first_binding is not None
        generation_two = RuntimeSessionBinding(
            session_id=agent_session.session_id,
            binding_id="binding-generation-2",
            generation=2,
            runtime_thread_id="thread-generation-2",
            runtime_workspace_id="workspace-generation-2",
        )
        assert await repo.get_or_add_session_binding(generation_two) == generation_two
        assert await repo.get_or_add_session_binding(first_binding) == first_binding
        await session.commit()

    async with factory() as session:
        repo = SqlalchemyAgentRepository(session)
        alias = replace(first_view.candidates[0], candidate_id="runtime-candidate-alias")
        assert await repo.get_or_add_candidate(alias) == first_view.candidates[0]
        await session.commit()

    async with factory() as session:
        repo = SqlalchemyAgentRepository(session)
        collision = replace(first_view.candidates[0], turn_run_id=second.run_id)
        with pytest.raises(RunConcurrentModificationError):
            await repo.get_or_add_candidate(collision)
        await session.rollback()

    runtime.invalid_candidate = True
    third = await service.post_message(
        actor,
        agent_session.session_id,
        content="生成非法候选",
        review_output_id=matrix.output_id,
        idempotency_key="agent-turn-invalid-candidate",
        correlation_id="invalid-candidate",
    )
    assert await runner.execute(third.run_id, "worker-3") is ExecutionOutcome.FAILED
    third_view = await service.get_turn(actor, third.run_id)
    assert third_view.run.status.value == "failed"
    assert third_view.candidates == ()
    messages = await service.list_messages(actor, agent_session.session_id)
    assert [message.sequence for message in messages] == [1, 2, 3, 4, 5]
    async with factory() as session:
        events = await SqlalchemyEventRepository(session).list_by_run(third.run_id)
        assert "agent_runtime_bound" not in {event.event_type for event in events}
        assert "agent_artifact_staged" not in {event.event_type for event in events}
        assert "agent_turn_succeeded" not in {event.event_type for event in events}
