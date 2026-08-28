"""SqlalchemyAgentRepository 的真实 PostgreSQL 幂等行为测试。"""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import func, select, update

from literature_agent.application.agent_artifact_service import (
    AGENT_ARTIFACT_MAX_TOTAL_BYTES_PER_TURN,
    AgentArtifactServiceError,
    AgentArtifactSubmissionService,
)
from literature_agent.application.agent_attachment_service import AgentAttachmentService
from literature_agent.application.ports.agent_artifact_source import AgentArtifactSourceScope
from literature_agent.application.ports.research_agent_runtime import RuntimeTurnRequest
from literature_agent.application.ports.runtime_execution_control import (
    RuntimeExecutionControl,
)
from literature_agent.domain.agent_attachment import AgentAttachmentStatus
from literature_agent.domain.evidence import AnswerStatus, create_claim_set
from literature_agent.domain.exceptions import (
    AgentAttachmentNotFoundError,
    AgentAttachmentReferencedError,
    IdempotencyConflictError,
    RunConcurrentModificationError,
)
from literature_agent.domain.research_agent import (
    AgentArtifactCandidate,
    AgentMessageRole,
    RuntimeSessionBinding,
    create_agent_artifact_candidate,
    create_agent_message,
)
from literature_agent.domain.run import RunStatus
from literature_agent.infrastructure.persistence.agent_attachment_repository import (
    SqlalchemyAgentAttachmentRepository,
)
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.models import (
    AgentAttachmentORM,
    AgentMessageAttachmentORM,
    AgentSessionORM,
    RunORM,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from tests.fakes.agent_scenario import make_agent_service, seed_agent_scenario
from tests.integration.conftest import db_engine as db_engine


class _Storage:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def write(self, key: str, content: bytes) -> None:
        self.values[key] = content

    async def read(self, key: str) -> bytes:
        return self.values[key]


class _ConcurrentStorage(_Storage):
    """强制两个上传都越过预查和事务外写入，再竞争数据库唯一事实。"""

    def __init__(self) -> None:
        super().__init__()
        self._arrived = 0
        self._ready = asyncio.Event()

    async def write(self, key: str, content: bytes) -> None:
        self._arrived += 1
        if self._arrived == 2:
            self._ready.set()
        await asyncio.wait_for(self._ready.wait(), timeout=5)
        await super().write(key, content)


def _attachment_service(scenario, storage):
    return AgentAttachmentService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        attachment_repo_factory=SqlalchemyAgentAttachmentRepository,
        storage=storage,
    )


def _artifact_submission_service(scenario) -> AgentArtifactSubmissionService:
    return AgentArtifactSubmissionService(
        session_factory=scenario.factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        storage=_Storage(),
        execution_control=cast(RuntimeExecutionControl, SimpleNamespace()),
    )


async def _running_artifact_request(
    scenario, *, title: str, idempotency_key: str
) -> RuntimeTurnRequest:
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=title
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="验证 Artifact 预算",
        review_output_id=scenario.matrix.output_id,
        idempotency_key=idempotency_key,
        correlation_id=idempotency_key,
    )
    async with scenario.factory() as session:
        await session.execute(
            update(RunORM)
            .where(RunORM.run_id == submitted.run_id)
            .values(status=RunStatus.RUNNING.value)
        )
        repo = SqlalchemyAgentRepository(session)
        turn = await repo.get_turn_scoped(submitted.run_id, scenario.actor.owner_id)
        assert turn is not None
        context = await repo.get_context_snapshot(turn.context_snapshot_id)
        policy = await repo.get_policy_snapshot(turn.policy_snapshot_id)
        assert context is not None and policy is not None
        await session.commit()
    return RuntimeTurnRequest(
        session_id=agent_session.session_id,
        turn_run_id=submitted.run_id,
        user_message_id=context.user_message_id,
        user_message_content="验证 Artifact 预算",
        context_snapshot=context,
        policy_snapshot=policy,
    )


def _validated_candidate(request: RuntimeTurnRequest, index: int, size_bytes: int):
    tool_call_id = f"budget-call-{index}"
    content_hash = hashlib.sha256(
        f"{request.turn_run_id}:{index}".encode()
    ).hexdigest()
    return create_agent_artifact_candidate(
        candidate_id=f"budget-candidate-{request.turn_run_id}-{index}",
        owner_id=request.context_snapshot.owner_id,
        project_id=request.context_snapshot.project_id,
        session_id=request.session_id,
        turn_run_id=request.turn_run_id,
        name=f"budget-{index}.txt",
        media_type="text/plain",
        content_ref=f"/workspace/outputs/budget-{index}.txt",
        content_hash=content_hash,
        size_bytes=size_bytes,
    ).validate(
        tool_call_id=tool_call_id,
        storage_key=f"agent-artifacts/staging/{content_hash}",
        sandbox_generation=1,
        sandbox_fencing_token=1,
    )


def _artifact_scope(request: RuntimeTurnRequest) -> AgentArtifactSourceScope:
    return AgentArtifactSourceScope(
        request.context_snapshot.owner_id,
        request.context_snapshot.project_id,
        request.session_id,
        request.turn_run_id,
        1,
        1,
    )


@pytest.mark.asyncio
async def test_concurrent_attachment_upload_converges_or_conflicts_cleanly(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_session = await make_agent_service(scenario.factory).create_session(
        scenario.actor, scenario.project.project_id, title=None
    )

    same_service = _attachment_service(scenario, _ConcurrentStorage())
    same_results = await asyncio.gather(
        *(
            same_service.upload(
                scenario.actor,
                agent_session.session_id,
                display_name="notes.txt",
                media_type="text/plain",
                content=b"same",
                idempotency_key="concurrent-same",
            )
            for _ in range(2)
        )
    )

    assert same_results[0].attachment == same_results[1].attachment
    assert sorted(item.replayed for item in same_results) == [False, True]

    conflict_service = _attachment_service(scenario, _ConcurrentStorage())
    conflict_results = await asyncio.gather(
        conflict_service.upload(
            scenario.actor,
            agent_session.session_id,
            display_name="notes.txt",
            media_type="text/plain",
            content=b"first",
            idempotency_key="concurrent-conflict",
        ),
        conflict_service.upload(
            scenario.actor,
            agent_session.session_id,
            display_name="notes.txt",
            media_type="text/plain",
            content=b"second",
            idempotency_key="concurrent-conflict",
        ),
        return_exceptions=True,
    )
    successes = [item for item in conflict_results if not isinstance(item, BaseException)]
    conflicts = [item for item in conflict_results if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], IdempotencyConflictError)

    async with scenario.factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AgentAttachmentORM)
            .where(
                AgentAttachmentORM.owner_id == scenario.actor.owner_id,
                AgentAttachmentORM.idempotency_key.in_(("concurrent-same", "concurrent-conflict")),
            )
        )
    assert count == 2


@pytest.mark.asyncio
async def test_message_and_delete_lock_attachment_until_one_outcome_is_durable(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)

    async def create_attachment(session_id: str, suffix: str):
        return await _attachment_service(scenario, _Storage()).upload(
            scenario.actor,
            session_id,
            display_name=f"notes-{suffix}.txt",
            media_type="text/plain",
            content=suffix.encode(),
            idempotency_key=f"lock-{suffix}",
        )

    # Message 先锁：删除必须等待消息/引用提交，然后稳定拒绝为 referenced。
    message_session = await make_agent_service(scenario.factory).create_session(
        scenario.actor, scenario.project.project_id, title="message-first"
    )
    message_attachment = await create_attachment(message_session.session_id, "message")
    message_locked = asyncio.Event()
    allow_message = asyncio.Event()
    delete_attempted = asyncio.Event()

    class MessageFirstRepository(SqlalchemyAgentAttachmentRepository):
        async def get_many_available_scoped(self, *args, for_update=False, **kwargs):
            values = await super().get_many_available_scoped(*args, for_update=for_update, **kwargs)
            if for_update:
                message_locked.set()
                await asyncio.wait_for(allow_message.wait(), timeout=5)
            return values

    class WaitingDeleteRepository(SqlalchemyAgentAttachmentRepository):
        async def get_scoped(self, *args, for_update=False, **kwargs):
            if for_update:
                delete_attempted.set()
            return await super().get_scoped(*args, for_update=for_update, **kwargs)

    message_service = make_agent_service(
        scenario.factory, attachment_repo_factory=MessageFirstRepository
    )
    delete_service = AgentAttachmentService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        attachment_repo_factory=WaitingDeleteRepository,
        storage=_Storage(),
    )
    message_task = asyncio.create_task(
        message_service.post_message(
            scenario.actor,
            message_session.session_id,
            content="先引用",
            review_output_id=scenario.matrix.output_id,
            attachment_ids=(message_attachment.attachment.attachment_id,),
            idempotency_key="message-first-turn",
            correlation_id="message-first-turn",
        )
    )
    await asyncio.wait_for(message_locked.wait(), timeout=5)
    delete_task = asyncio.create_task(
        delete_service.delete(
            scenario.actor,
            message_session.session_id,
            message_attachment.attachment.attachment_id,
        )
    )
    await asyncio.wait_for(delete_attempted.wait(), timeout=5)
    allow_message.set()
    message_result = await asyncio.wait_for(message_task, timeout=5)
    with pytest.raises(AgentAttachmentReferencedError):
        await asyncio.wait_for(delete_task, timeout=5)

    # Delete 先锁：消息必须等待删除提交，然后把附件视为不可用且不创建引用。
    delete_session = await make_agent_service(scenario.factory).create_session(
        scenario.actor, scenario.project.project_id, title="delete-first"
    )
    delete_attachment = await create_attachment(delete_session.session_id, "delete")
    delete_locked = asyncio.Event()
    allow_delete = asyncio.Event()
    message_attempted = asyncio.Event()

    class DeleteFirstRepository(SqlalchemyAgentAttachmentRepository):
        async def get_scoped(self, *args, for_update=False, **kwargs):
            value = await super().get_scoped(*args, for_update=for_update, **kwargs)
            if for_update:
                delete_locked.set()
                await asyncio.wait_for(allow_delete.wait(), timeout=5)
            return value

    class WaitingMessageRepository(SqlalchemyAgentAttachmentRepository):
        async def get_many_available_scoped(self, *args, for_update=False, **kwargs):
            if for_update:
                message_attempted.set()
            return await super().get_many_available_scoped(*args, for_update=for_update, **kwargs)

    delete_first_service = AgentAttachmentService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        attachment_repo_factory=DeleteFirstRepository,
        storage=_Storage(),
    )
    waiting_message_service = make_agent_service(
        scenario.factory, attachment_repo_factory=WaitingMessageRepository
    )
    delete_first_task = asyncio.create_task(
        delete_first_service.delete(
            scenario.actor,
            delete_session.session_id,
            delete_attachment.attachment.attachment_id,
        )
    )
    await asyncio.wait_for(delete_locked.wait(), timeout=5)
    waiting_message_task = asyncio.create_task(
        waiting_message_service.post_message(
            scenario.actor,
            delete_session.session_id,
            content="后引用",
            review_output_id=scenario.matrix.output_id,
            attachment_ids=(delete_attachment.attachment.attachment_id,),
            idempotency_key="delete-first-turn",
            correlation_id="delete-first-turn",
        )
    )
    await asyncio.wait_for(message_attempted.wait(), timeout=5)
    allow_delete.set()
    await asyncio.wait_for(delete_first_task, timeout=5)
    with pytest.raises(AgentAttachmentNotFoundError):
        await asyncio.wait_for(waiting_message_task, timeout=5)

    async with scenario.factory() as session:
        repo = SqlalchemyAgentAttachmentRepository(session)
        message_winner = await repo.get_scoped(
            message_attachment.attachment.attachment_id,
            message_session.session_id,
            scenario.actor.owner_id,
        )
        delete_winner = await repo.get_scoped(
            delete_attachment.attachment.attachment_id,
            delete_session.session_id,
            scenario.actor.owner_id,
        )
        message_refs = await session.scalar(
            select(func.count())
            .select_from(AgentMessageAttachmentORM)
            .where(
                AgentMessageAttachmentORM.attachment_id
                == message_attachment.attachment.attachment_id
            )
        )
        delete_refs = await session.scalar(
            select(func.count())
            .select_from(AgentMessageAttachmentORM)
            .where(
                AgentMessageAttachmentORM.attachment_id
                == delete_attachment.attachment.attachment_id
            )
        )

    assert message_result.run_id
    assert message_winner is not None
    assert message_winner.status is AgentAttachmentStatus.AVAILABLE
    assert message_refs == 1
    assert delete_winner is not None
    assert delete_winner.status is AgentAttachmentStatus.DELETED
    assert delete_refs == 0


@pytest.mark.asyncio
async def test_attachment_message_and_context_snapshot_round_trip(db_engine) -> None:
    scenario = await seed_agent_scenario(db_engine)
    agent_service = make_agent_service(scenario.factory)
    agent_session = await agent_service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    attachment_service = AgentAttachmentService(
        session_factory=scenario.factory,
        agent_repo_factory=SqlalchemyAgentRepository,
        attachment_repo_factory=SqlalchemyAgentAttachmentRepository,
        storage=_Storage(),
    )
    uploaded = await attachment_service.upload(
        scenario.actor,
        agent_session.session_id,
        display_name="notes.txt",
        media_type="text/plain",
        content=b"notes",
        idempotency_key="pg-attachment-1",
    )
    submitted = await agent_service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="读取附件",
        review_output_id=scenario.matrix.output_id,
        attachment_ids=(uploaded.attachment.attachment_id,),
        idempotency_key="pg-attachment-turn",
        correlation_id="pg-attachment-turn",
    )

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        message = await repo.get_message_by_run_and_role(submitted.run_id, "user")
        turn = await repo.get_turn_scoped(submitted.run_id, scenario.actor.owner_id)
        assert message is not None and turn is not None
        snapshot = await repo.get_context_snapshot(turn.context_snapshot_id)

    assert message.attachment_ids == (uploaded.attachment.attachment_id,)
    assert snapshot is not None
    assert snapshot.schema_version == "agent-context.v2"
    assert snapshot.attachment_refs[0].content_hash == uploaded.attachment.content_hash


@pytest.mark.asyncio
async def test_repository_reads_exact_binding_generation_and_converges_candidate_fact(
    db_engine,
) -> None:
    """旧 generation 重放不能漂移，turn/hash 重放只能收敛到同一事实。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="Repository 行为",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="repository-turn-1",
        correlation_id="repository-submit",
    )
    generation_one = RuntimeSessionBinding(
        session_id=agent_session.session_id,
        binding_id="repository-binding-1",
        generation=1,
        runtime_thread_id="repository-thread-1",
        runtime_workspace_id="repository-workspace-1",
    )
    generation_two = replace(
        generation_one,
        binding_id="repository-binding-2",
        generation=2,
        runtime_thread_id="repository-thread-2",
        runtime_workspace_id="repository-workspace-2",
    )
    candidate = create_agent_artifact_candidate(
        candidate_id="repository-candidate-1",
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        session_id=agent_session.session_id,
        turn_run_id=submitted.run_id,
        name="notes.md",
        media_type="text/markdown",
        content_ref="runtime://repository-candidate-1",
        content_hash="d" * 64,
        size_bytes=12,
    )

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        assert await repo.get_or_add_session_binding(generation_one) == generation_one
        assert await repo.get_or_add_session_binding(generation_two) == generation_two
        assert await repo.get_or_add_session_binding(generation_one) == generation_one
        assert (
            await repo.get_session_binding_generation(agent_session.session_id, 1) == generation_one
        )
        assert await repo.get_session_binding(agent_session.session_id) == generation_two
        saved = await repo.get_or_add_candidate(candidate)
        alias = replace(candidate, candidate_id="repository-candidate-alias")
        assert await repo.get_or_add_candidate(alias) == saved
        await session.commit()


@pytest.mark.asyncio
async def test_repository_lists_project_sessions_in_stable_activity_order(db_engine) -> None:
    """列表必须在单个 owner/Project 内稳定倒序，且空 Project 返回空列表。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    first = await service.create_session(
        scenario.actor, scenario.project.project_id, title="较早会话"
    )
    second = await service.create_session(
        scenario.actor, scenario.project.project_id, title="最近会话"
    )
    base = datetime(2026, 8, 27, tzinfo=UTC)

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        # 直接更新领域时间只为固定排序契约；不依赖创建调用的时钟先后。
        await session.execute(
            update(AgentSessionORM)
            .where(AgentSessionORM.session_id == first.session_id)
            .values(last_activity_at=base)
        )
        await session.execute(
            update(AgentSessionORM)
            .where(AgentSessionORM.session_id == second.session_id)
            .values(last_activity_at=base + timedelta(minutes=1))
        )
        await session.commit()

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        listed = await repo.list_sessions_scoped(
            scenario.project.project_id, scenario.actor.owner_id
        )
        empty = await repo.list_sessions_scoped("missing-project", scenario.actor.owner_id)

    assert [value.session_id for value in listed] == [second.session_id, first.session_id]
    assert empty == []


@pytest.mark.asyncio
async def test_repository_rejects_candidate_id_collision_with_different_scope(db_engine) -> None:
    """Runtime 复用 candidate_id 时不能把其他 owner/事实冒充为当前 Turn 结果。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="Candidate 碰撞",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="repository-collision-turn",
        correlation_id="repository-collision",
    )
    candidate = create_agent_artifact_candidate(
        candidate_id="repository-collision",
        owner_id=scenario.actor.owner_id,
        project_id=scenario.project.project_id,
        session_id=agent_session.session_id,
        turn_run_id=submitted.run_id,
        name="notes.md",
        media_type="text/markdown",
        content_ref="runtime://repository-collision",
        content_hash="e" * 64,
        size_bytes=8,
    )
    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        await repo.get_or_add_candidate(candidate)
        with pytest.raises(RunConcurrentModificationError):
            await repo.get_or_add_candidate(replace(candidate, owner_id="other-owner"))
        await session.rollback()


@pytest.mark.asyncio
async def test_artifact_count_budget_is_serialized_by_run_lock_and_replay_is_free(
    db_engine,
) -> None:
    """第 8/9 项竞争必须只有一个提交；同一 tool_call 重放不重复计数。"""
    scenario = await seed_agent_scenario(db_engine)
    request = await _running_artifact_request(
        scenario,
        title="artifact-count-budget",
        idempotency_key="artifact-count-budget-turn",
    )
    service = _artifact_submission_service(scenario)
    scope = _artifact_scope(request)
    for index in range(7):
        await service._record_candidate(
            request, scope, _validated_candidate(request, index, 1)
        )

    contenders = (
        _validated_candidate(request, 7, 1),
        _validated_candidate(request, 8, 1),
    )
    results = await asyncio.gather(
        *(service._record_candidate(request, scope, value) for value in contenders),
        return_exceptions=True,
    )
    failures = [value for value in results if isinstance(value, BaseException)]

    successes = [value for value in results if isinstance(value, AgentArtifactCandidate)]
    assert len(successes) == 1
    assert len(failures) == 1
    failure = failures[0]
    success = successes[0]
    assert isinstance(failure, AgentArtifactServiceError)
    assert isinstance(success, AgentArtifactCandidate)
    assert failure.code == "artifact_turn_budget_exceeded"

    replayed = await service._record_candidate(request, scope, success)
    assert replayed.candidate_id == success.candidate_id
    async with scenario.factory() as session:
        candidates = await SqlalchemyAgentRepository(session).list_candidates_scoped(
            request.turn_run_id, scenario.actor.owner_id
        )
    assert len(candidates) == 8
    assert all(value.status.value == "validated" for value in candidates)


@pytest.mark.asyncio
async def test_artifact_total_budget_rejects_bytes_above_fifty_mib_without_staged_row(
    db_engine,
) -> None:
    scenario = await seed_agent_scenario(db_engine)
    request = await _running_artifact_request(
        scenario,
        title="artifact-byte-budget",
        idempotency_key="artifact-byte-budget-turn",
    )
    service = _artifact_submission_service(scenario)
    scope = _artifact_scope(request)
    ten_mib = 10 * 1024 * 1024
    for index in range(5):
        await service._record_candidate(
            request, scope, _validated_candidate(request, index, ten_mib)
        )

    with pytest.raises(AgentArtifactServiceError) as caught:
        await service._record_candidate(
            request, scope, _validated_candidate(request, 5, 1)
        )
    assert caught.value.code == "artifact_turn_budget_exceeded"

    async with scenario.factory() as session:
        candidates = await SqlalchemyAgentRepository(session).list_candidates_scoped(
            request.turn_run_id, scenario.actor.owner_id
        )
    assert len(candidates) == 5
    assert sum(value.size_bytes for value in candidates) == (
        AGENT_ARTIFACT_MAX_TOTAL_BYTES_PER_TURN
    )


@pytest.mark.asyncio
async def test_agent_message_round_trip_preserves_nullable_claim_set_only_on_message(
    db_engine,
) -> None:
    """claim_set_id 只属于 Message；Session 映射不得读取或写入该列。"""
    scenario = await seed_agent_scenario(db_engine)
    service = make_agent_service(scenario.factory)
    agent_session = await service.create_session(
        scenario.actor, scenario.project.project_id, title=None
    )
    submitted = await service.post_message(
        scenario.actor,
        agent_session.session_id,
        content="引用结果",
        review_output_id=scenario.matrix.output_id,
        idempotency_key="repository-claim-turn",
        correlation_id="repository-claim",
    )

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        claim_set = create_claim_set(submitted.run_id, AnswerStatus.ANSWERED)
        await SqlalchemyClaimSetRepository(session).add_claim_set(claim_set)
        await session.flush()
        sequence = await repo.allocate_message_sequence(agent_session.session_id)
        assistant = create_agent_message(
            session_id=agent_session.session_id,
            last_sequence=sequence - 1,
            role=AgentMessageRole.ASSISTANT,
            content="结论 [evidence:e-1]",
            turn_run_id=submitted.run_id,
            idempotency_key=f"assistant:{submitted.run_id}",
            claim_set_id=claim_set.claim_set_id,
        )
        await repo.add_message(assistant)
        await session.commit()

    async with scenario.factory() as session:
        repo = SqlalchemyAgentRepository(session)
        restored_session = await repo.get_session_scoped(
            agent_session.session_id, scenario.actor.owner_id
        )
        restored_message = await repo.get_message_by_run_and_role(
            submitted.run_id, AgentMessageRole.ASSISTANT.value
        )
        assert restored_session is not None
        assert restored_session.session_id == agent_session.session_id
        assert restored_session.owner_id == agent_session.owner_id
        assert restored_session.project_id == agent_session.project_id
        assert restored_session.active_turn_run_id == submitted.run_id
        assert restored_message is not None
        assert restored_message.claim_set_id == claim_set.claim_set_id
