"""Project Research Context 的 PostgreSQL effect、Evidence 与授权闭环。"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import update

from literature_agent.application.project_research_context_service import (
    ProjectResearchContextError,
    ProjectResearchContextService,
)
from literature_agent.application.retriever import RetrievalResult
from literature_agent.application.run_service import RunService
from literature_agent.domain.chunk import Chunk
from literature_agent.domain.evidence import create_evidence
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.models import (
    AgentContextSnapshotORM,
    AgentPolicySnapshotORM,
    ReviewOutputORM,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from literature_agent.infrastructure.persistence.tool_execution_repository import (
    SqlalchemyToolExecutionRepository,
)
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine


class _TrackedFactory:
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


class _Retriever:
    def __init__(self, tracked: _TrackedFactory) -> None:
        self.tracked = tracked
        self.calls: list[dict] = []
        self.result: RetrievalResult | None = None

    async def retrieve_for_scope(self, **kwargs):
        assert self.tracked.active == 0
        self.calls.append(kwargs)
        return [self.result] if self.result is not None else []


class _BlockingRetriever(_Retriever):
    def __init__(self, tracked: _TrackedFactory) -> None:
        super().__init__(tracked)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def retrieve_for_scope(self, **kwargs):
        assert self.tracked.active == 0
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return []


class _FlakyRetriever(_Retriever):
    def __init__(self, tracked: _TrackedFactory) -> None:
        super().__init__(tracked)
        self.fail_once = True

    async def retrieve_for_scope(self, **kwargs):
        assert self.tracked.active == 0
        self.calls.append(kwargs)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("不得泄漏的 Retriever 原始错误")
        return []


@pytest.mark.asyncio
async def test_search_replays_stable_effect_and_materializes_agent_run_evidence(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="检索图神经网络",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="project-context-turn",
        correlation_id="project-context",
    )
    view = await agent_service.get_turn(scenario.actor, submitted.run_id)
    ref = view.context_snapshot.project_index_refs[0]
    chunk = Chunk(
        chunk_id="context-chunk-1",
        chunk_set_id=ref.chunk_set_id,
        sequence=1,
        text="图神经网络在该任务上提升准确率。",
        token_count=20,
        section_path="Results",
        page_start=3,
        page_end=3,
        content_hash="d" * 64,
    )
    async with scenario.factory() as session:
        await SqlalchemyChunkRepository(session).add_many([chunk])
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(
                allowed_tool_names=[
                    "search_project_chunks",
                    "read_review_evidence_matrix",
                ],
                max_tool_calls=2,
            )
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3
        )
        await session.commit()

    tracked = _TrackedFactory(scenario.factory)
    retriever = _Retriever(tracked)
    retriever.result = RetrievalResult(
        chunk=chunk,
        paper_id=ref.paper_id,
        version_id=ref.paper_version_id,
        semantic_rank=1,
        fts_rank=None,
        rrf_score=1.0,
        rank=1,
    )
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=retriever,
    )

    first = await service.search_project_chunks(submitted.run_id, query="graph")
    replay = await service.search_project_chunks(submitted.run_id, query="graph")

    assert replay == first
    assert len(retriever.calls) == 1
    assert retriever.calls[0]["version_scope"] == [
        (ref.paper_id, ref.paper_version_id)
    ]
    assert retriever.calls[0]["chunk_set_scope"] == [ref.chunk_set_id]
    assert first.payload["items"][0]["evidence_id"]
    async with scenario.factory() as session:
        evidence = await SqlalchemyEvidenceRepository(session).list_by_run(submitted.run_id)
        effects = await SqlalchemyToolExecutionRepository(session).list_by_turn(
            submitted.run_id
        )
        events = await SqlalchemyEventRepository(session).list_by_run(submitted.run_id)
        assert len(evidence) == len(effects) == 1
        assert effects[0].attempt_count == 1
        assert [event.event_type for event in events[-2:]] == [
            "agent_tool_started",
            "agent_tool_succeeded",
        ]
        assert all("graph" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_running_duplicate_effect_is_rejected_without_second_retrieval(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="并发重复 effect",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="concurrent-effect-turn",
        correlation_id="concurrent-effect-turn",
    )
    async with scenario.factory() as session:
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(allowed_tool_names=["search_project_chunks"], max_tool_calls=1)
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3
        )
        await session.commit()
    tracked = _TrackedFactory(scenario.factory)
    retriever = _BlockingRetriever(tracked)
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=retriever,
    )
    first = asyncio.create_task(
        service.search_project_chunks(submitted.run_id, query="same query")
    )
    await asyncio.wait_for(retriever.started.wait(), timeout=5)

    with pytest.raises(ProjectResearchContextError) as exc_info:
        await service.search_project_chunks(submitted.run_id, query="same query")

    assert exc_info.value.code == "project_context_effect_in_progress"
    assert len(retriever.calls) == 1
    retriever.release.set()
    await asyncio.wait_for(first, timeout=5)


@pytest.mark.asyncio
async def test_temporary_effect_retry_reuses_budget_then_new_effect_is_rejected(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="临时失败重试",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="temporary-effect-turn",
        correlation_id="temporary-effect-turn",
    )
    async with scenario.factory() as session:
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(allowed_tool_names=["search_project_chunks"], max_tool_calls=1)
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3
        )
        await session.commit()
    tracked = _TrackedFactory(scenario.factory)
    retriever = _FlakyRetriever(tracked)
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=retriever,
    )

    with pytest.raises(ProjectResearchContextError) as first_error:
        await service.search_project_chunks(submitted.run_id, query="retry me")
    recovered = await service.search_project_chunks(
        submitted.run_id, query="retry me"
    )
    with pytest.raises(ProjectResearchContextError) as budget_error:
        await service.search_project_chunks(submitted.run_id, query="new effect")

    assert first_error.value.code == "project_context_retrieval_unavailable"
    assert recovered.payload["items"] == []
    assert budget_error.value.code == "project_context_tool_budget_exceeded"
    assert len(retriever.calls) == 2
    async with scenario.factory() as session:
        effects = await SqlalchemyToolExecutionRepository(session).list_by_turn(
            submitted.run_id
        )
        assert len(effects) == 1
        assert effects[0].attempt_count == 2


@pytest.mark.asyncio
async def test_cancelled_turn_rejects_new_context_tool_before_retrieval(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="取消后不得检索",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="cancelled-project-context",
        correlation_id="cancelled-project-context",
    )
    async with scenario.factory() as session:
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(allowed_tool_names=["search_project_chunks"], max_tool_calls=1)
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.CANCEL_REQUESTED, 3
        )
        await session.commit()
    tracked = _TrackedFactory(scenario.factory)
    retriever = _Retriever(tracked)
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=retriever,
    )

    with pytest.raises(ProjectResearchContextError) as exc_info:
        await service.search_project_chunks(submitted.run_id, query="graph")

    assert exc_info.value.code == "project_context_cancelled"
    assert retriever.calls == []


@pytest.mark.asyncio
async def test_inflight_retrieval_cannot_commit_after_cancel_request(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="检索期间取消",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="inflight-cancel-turn",
        correlation_id="inflight-cancel-turn",
    )
    async with scenario.factory() as session:
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(allowed_tool_names=["search_project_chunks"], max_tool_calls=1)
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3
        )
        await session.commit()
    tracked = _TrackedFactory(scenario.factory)
    retriever = _BlockingRetriever(tracked)
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=retriever,
    )
    task = asyncio.create_task(
        service.search_project_chunks(submitted.run_id, query="cancel race")
    )
    await asyncio.wait_for(retriever.started.wait(), timeout=5)
    await RunService(
        scenario.factory,
        SqlalchemyRunRepository,
        SqlalchemyEventRepository,
    ).cancel_run(scenario.actor, submitted.run_id, "cancel-race")
    retriever.release.set()

    with pytest.raises(ProjectResearchContextError) as exc_info:
        await asyncio.wait_for(task, timeout=5)

    assert exc_info.value.code == "project_context_cancelled"
    assert len(retriever.calls) == 1
    async with scenario.factory() as session:
        assert (
            await SqlalchemyEvidenceRepository(session).list_by_run(submitted.run_id)
            == []
        )
        effects = await SqlalchemyToolExecutionRepository(session).list_by_turn(
            submitted.run_id
        )
        events = await SqlalchemyEventRepository(session).list_by_run(
            submitted.run_id
        )
        assert len(effects) == 1
        assert effects[0].status.value == "failed"
        assert effects[0].error_kind is not None
        assert effects[0].error_kind.value == "cancelled"
        assert "agent_tool_succeeded" not in {
            event.event_type for event in events
        }


@pytest.mark.asyncio
async def test_matrix_reader_clones_only_bounded_return_rows_into_agent_run(
    db_engine,
) -> None:
    """Reader 可验证完整聚合，但只物化稳定截断后实际暴露的 Evidence。"""
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="读取矩阵",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="matrix-bounded-turn",
        correlation_id="matrix-bounded-turn",
    )
    view = await agent_service.get_turn(scenario.actor, submitted.run_id)
    ref = view.context_snapshot.project_index_refs[0]
    async with scenario.factory() as session:
        chunk_set = await SqlalchemyChunkSetRepository(session).get_by_id(
            ref.chunk_set_id
        )
        assert chunk_set is not None
        chunks = [
            Chunk(
                chunk_id=f"matrix-chunk-{index:02d}",
                chunk_set_id=ref.chunk_set_id,
                sequence=index + 1,
                text=f"矩阵证据 {index}",
                token_count=10,
                section_path="Results",
                page_start=index + 1,
                page_end=index + 1,
                content_hash=f"{index:064x}",
            )
            for index in range(14)
        ]
        await SqlalchemyChunkRepository(session).add_many(chunks)
        source_evidence = [
            create_evidence(
                run_id=scenario.matrix.review_run_id,
                project_id=scenario.project.project_id,
                paper_id=ref.paper_id,
                version_id=ref.paper_version_id,
                parse_revision_id=chunk_set.parse_revision_id,
                chunk_id=chunk.chunk_id,
                section_path=chunk.section_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                excerpt=chunk.text,
            )
            for chunk in chunks
        ]
        await SqlalchemyEvidenceRepository(session).add_many(source_evidence)
        rows = [
            {
                "paper_id": ref.paper_id,
                "dimension_key": f"dimension_{index:02d}",
                "status": "extracted",
                "finding": f"发现 {index}",
                "limitations": None,
                "evidence_ids": [source_evidence[index].evidence_id],
            }
            for index in reversed(range(14))
        ]
        await session.execute(
            update(ReviewOutputORM)
            .where(ReviewOutputORM.output_id == scenario.matrix.output_id)
            .values(
                payload={
                    "rows": rows,
                    "paper_failures": [],
                    "summary": {"valid_papers": 1, "failed_papers": 0},
                }
            )
        )
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(
                allowed_tool_names=["read_review_evidence_matrix"],
                max_tool_calls=1,
            )
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3
        )
        await session.commit()

    tracked = _TrackedFactory(scenario.factory)
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        chunk_repo_factory=SqlalchemyChunkRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=_Retriever(tracked),
    )

    result = await service.read_review_evidence_matrix(submitted.run_id)
    replay = await service.read_review_evidence_matrix(submitted.run_id)

    assert replay == result
    assert result.payload["returned_count"] == 12
    assert result.payload["truncated"] is True
    assert [row["dimension_key"] for row in result.payload["rows"]] == [
        f"dimension_{index:02d}" for index in range(12)
    ]
    returned_ids = {
        evidence_id
        for row in result.payload["rows"]
        for evidence_id in row["evidence_ids"]
    }
    assert returned_ids.isdisjoint(
        {item.evidence_id for item in source_evidence}
    )
    async with scenario.factory() as session:
        cloned = await SqlalchemyEvidenceRepository(session).list_by_run(
            submitted.run_id
        )
        effects = await SqlalchemyToolExecutionRepository(session).list_by_turn(
            submitted.run_id
        )
        events = await SqlalchemyEventRepository(session).list_by_run(
            submitted.run_id
        )
        assert len(cloned) == 12
        assert len(effects) == 1
        assert [event.event_type for event in events].count("agent_tool_started") == 1
        assert [event.event_type for event in events].count("agent_tool_succeeded") == 1
        assert {item.evidence_id for item in cloned} == returned_ids
        assert {item.chunk_id for item in cloned} == {
            f"matrix-chunk-{index:02d}" for index in range(12)
        }


@pytest.mark.asyncio
async def test_matrix_reader_rejects_cross_run_evidence_beyond_returned_rows(
    db_engine,
) -> None:
    """截断范围外的 source Evidence 也必须通过完整 Snapshot 闭包校验。"""
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="拒绝截断范围外的越权证据",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="matrix-hidden-cross-run",
        correlation_id="matrix-hidden-cross-run",
    )
    view = await agent_service.get_turn(scenario.actor, submitted.run_id)
    ref = view.context_snapshot.project_index_refs[0]
    async with scenario.factory() as session:
        chunk_set = await SqlalchemyChunkSetRepository(session).get_by_id(
            ref.chunk_set_id
        )
        assert chunk_set is not None
        foreign_run = create_run(
            scenario.project.project_id,
            scenario.actor.owner_id,
            RunType.REVIEW,
        )
        await SqlalchemyRunRepository(session).add(foreign_run)
        await session.flush()
        chunks = [
            Chunk(
                chunk_id=f"hidden-cross-run-chunk-{index:02d}",
                chunk_set_id=ref.chunk_set_id,
                sequence=index + 1,
                text=f"隐藏范围证据 {index}",
                token_count=10,
                section_path="Results",
                page_start=index + 1,
                page_end=index + 1,
                content_hash=f"{index + 100:064x}",
            )
            for index in range(13)
        ]
        await SqlalchemyChunkRepository(session).add_many(chunks)
        source_evidence = [
            create_evidence(
                run_id=(
                    scenario.matrix.review_run_id if index < 12 else foreign_run.run_id
                ),
                project_id=scenario.project.project_id,
                paper_id=ref.paper_id,
                version_id=ref.paper_version_id,
                parse_revision_id=chunk_set.parse_revision_id,
                chunk_id=chunk.chunk_id,
                section_path=chunk.section_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                excerpt=chunk.text,
            )
            for index, chunk in enumerate(chunks)
        ]
        await SqlalchemyEvidenceRepository(session).add_many(source_evidence)
        await session.execute(
            update(ReviewOutputORM)
            .where(ReviewOutputORM.output_id == scenario.matrix.output_id)
            .values(
                payload={
                    "rows": [
                        {
                            "paper_id": ref.paper_id,
                            "dimension_key": f"dimension_{index:02d}",
                            "status": "extracted",
                            "finding": f"发现 {index}",
                            "limitations": None,
                            "evidence_ids": [source_evidence[index].evidence_id],
                        }
                        for index in range(13)
                    ],
                    "paper_failures": [],
                    "summary": {"valid_papers": 1, "failed_papers": 0},
                }
            )
        )
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(
                allowed_tool_names=["read_review_evidence_matrix"],
                max_tool_calls=1,
            )
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3
        )
        await session.commit()

    tracked = _TrackedFactory(scenario.factory)
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        chunk_repo_factory=SqlalchemyChunkRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=_Retriever(tracked),
    )

    with pytest.raises(ProjectResearchContextError) as exc_info:
        await service.read_review_evidence_matrix(submitted.run_id)

    assert exc_info.value.code == "project_context_matrix_invalid"
    assert exc_info.value.kind.value == "permanent"
    async with scenario.factory() as session:
        assert (
            await SqlalchemyEvidenceRepository(session).list_by_run(submitted.run_id)
            == []
        )
        effects = await SqlalchemyToolExecutionRepository(session).list_by_turn(
            submitted.run_id
        )
        events = await SqlalchemyEventRepository(session).list_by_run(
            submitted.run_id
        )
        assert len(effects) == 1
        assert effects[0].status.value == "failed"
        assert effects[0].error_kind is not None
        assert effects[0].error_kind.value == "permanent"
        assert "agent_tool_succeeded" not in {
            event.event_type for event in events
        }


@pytest.mark.asyncio
async def test_matrix_reader_rejects_foreign_owner_output_even_if_snapshot_is_tampered(
    db_engine,
) -> None:
    mine = await seed_agent_scenario(db_engine, owner_id="owner-mine")
    foreign = await seed_agent_scenario(db_engine, owner_id="owner-foreign")
    agent_service = make_agent_service(mine.factory)
    agent_session = await agent_service.create_session(
        mine.actor, mine.project.project_id, title=None
    )
    submitted = await agent_service.post_message(
        mine.actor,
        agent_session.session_id,
        content="不得读取他人矩阵",
        review_output_id=mine.matrix.output_id,
        idempotency_key="foreign-matrix-turn",
        correlation_id="foreign-matrix-turn",
    )
    async with mine.factory() as session:
        await session.execute(
            update(AgentContextSnapshotORM)
            .where(AgentContextSnapshotORM.turn_run_id == submitted.run_id)
            .values(review_output_id=foreign.matrix.output_id)
        )
        await session.execute(
            update(AgentPolicySnapshotORM)
            .where(AgentPolicySnapshotORM.turn_run_id == submitted.run_id)
            .values(
                allowed_tool_names=["read_review_evidence_matrix"],
                max_tool_calls=1,
            )
        )
        assert await SqlalchemyRunRepository(session).update_status(
            submitted.run_id, RunStatus.QUEUED, RunStatus.RUNNING, 3
        )
        await session.commit()
    tracked = _TrackedFactory(mine.factory)
    service = ProjectResearchContextService(
        session_factory=tracked,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        chunk_repo_factory=SqlalchemyChunkRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=_Retriever(tracked),
    )

    with pytest.raises(ProjectResearchContextError) as exc_info:
        await service.read_review_evidence_matrix(submitted.run_id)

    assert exc_info.value.code == "project_context_matrix_invalid"
    async with mine.factory() as session:
        assert (
            await SqlalchemyEvidenceRepository(session).list_by_run(submitted.run_id)
            == []
        )
