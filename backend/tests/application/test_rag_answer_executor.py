"""RagAnswerExecutor 应用服务测试（切片 8）。"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from literature_agent.application.evidence_service import EvidenceService
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.rag_answer_executor import (
    INSUFFICIENT_EVIDENCE_TEXT,
    RagAnswerExecutor,
)
from literature_agent.application.retriever import Retriever
from literature_agent.application.run_execution_service import (
    ExecutionOutcome,
    RunExecutionService,
)
from literature_agent.domain.chunk import Chunk, create_chunk_set
from literature_agent.domain.conversation import (
    MessageRole,
    ScopeMode,
    create_conversation,
    create_message,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.evidence import (
    RUN_INPUT_VERSION_SCOPE_KEY,
    AnswerStatus,
    create_claim,
    create_claim_set,
)
from literature_agent.domain.model_errors import ModelAuthError, ModelRateLimitError
from literature_agent.domain.model_invocation import InvocationStatus
from literature_agent.domain.model_types import ChatMessage, ChatResult, ModelUsage
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.retrieval import RetrievedChunk
from literature_agent.domain.run import Run, RunStatus, RunType, create_run
from literature_agent.infrastructure.models.fake_models import (
    FakeChatModel as EvidenceDrivenFakeChatModel,
)
from tests.fakes.fake_attempt_repository import FakeAttemptRepository
from tests.fakes.fake_chunk_repository import FakeChunkRepository
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_claim_set_repository import FakeClaimSetRepository
from tests.fakes.fake_conversation_repository import FakeConversationRepository
from tests.fakes.fake_embedding_model import FakeEmbeddingModel
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_evidence_repository import FakeEvidenceRepository
from tests.fakes.fake_message_repository import FakeMessageRepository
from tests.fakes.fake_model_invocation_repository import (
    FakeModelInvocationRepository,
)
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository

_PROJECT_ID = "p-1"
_OWNER_ID = "user-1"
_PAPER_ID = "paper-1"
_VERSION_ID = "version-1"
_REVISION_ID = "rev-1"


class _ScriptedChatModel:
    """按调用次序脚本化响应/副作用的 Chat 假实现（1 起计数）。

    脚本耗尽后委托生产侧证据 ID 驱动的 FakeChatModel，保证修复重试
    能拿到引用真实 Evidence ID 的合法输出。
    """

    provider = "fake"
    model = "fake-chat"

    def __init__(
        self,
        *,
        responses: list[str | Exception] | None = None,
        on_call: Any = None,
    ) -> None:
        self._inner = EvidenceDrivenFakeChatModel()
        self._responses = list(responses or [])
        self._on_call = on_call
        self.calls: list[list[ChatMessage]] = []

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """记录调用；按脚本触发副作用/抛错/返回，否则委托证据驱动 Fake。"""
        self.calls.append(list(messages))
        if self._on_call is not None:
            self._on_call(len(self.calls))
        if self._responses:
            queued = self._responses.pop(0)
            if isinstance(queued, Exception):
                raise queued
            return ChatResult(
                content=queued,
                model=self.model,
                usage=ModelUsage(prompt_tokens=10, completion_tokens=5),
            )
        return await self._inner.generate(
            messages, json_schema=json_schema, max_tokens=max_tokens
        )


class _ScriptedEmbeddingModel:
    """带 on_call 钩子的 Embedding 假实现（用于检索期间注入取消）。"""

    provider = "fake"
    model = "fake-embedding"

    def __init__(self, *, on_call: Any = None) -> None:
        self._inner = FakeEmbeddingModel(dimensions=1024)
        self._on_call = on_call
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]):
        """记录调用并触发副作用，然后委托确定性 Fake。"""
        self.calls.append(list(texts))
        if self._on_call is not None:
            self._on_call(len(self.calls))
        return await self._inner.embed(texts)


@pytest.fixture
def run_repo() -> FakeRunRepository:
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    return FakeEventRepository()


@pytest.fixture
def conversation_repo() -> FakeConversationRepository:
    return FakeConversationRepository()


@pytest.fixture
def message_repo() -> FakeMessageRepository:
    return FakeMessageRepository()


@pytest.fixture
def claim_set_repo() -> FakeClaimSetRepository:
    return FakeClaimSetRepository()


@pytest.fixture
def evidence_repo() -> FakeEvidenceRepository:
    return FakeEvidenceRepository()


@pytest.fixture
def chunk_set_repo() -> FakeChunkSetRepository:
    return FakeChunkSetRepository()


@pytest.fixture
def chunk_repo() -> FakeChunkRepository:
    return FakeChunkRepository()


@pytest.fixture
def attempt_repo() -> FakeAttemptRepository:
    return FakeAttemptRepository()


@pytest.fixture
def outbox_repo() -> FakeOutboxRepository:
    return FakeOutboxRepository()


@pytest.fixture
def invocation_repo() -> FakeModelInvocationRepository:
    return FakeModelInvocationRepository()


def _make_executor(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
    *,
    chat_model=None,
    embedding_model=None,
) -> RagAnswerExecutor:
    """构建使用 Fake 依赖的 RagAnswerExecutor（含 Retriever 与 ModelGateway）。"""
    gateway = ModelGateway(
        embedding_model=embedding_model or FakeEmbeddingModel(dimensions=1024),
        chat_model=chat_model or EvidenceDrivenFakeChatModel(),
        session_factory=fake_session,
        invocation_repo_factory=lambda _s: invocation_repo,
    )
    retriever = Retriever(
        session_factory=fake_session,
        chunk_repo_factory=lambda _s: chunk_repo,
        model_gateway=gateway,
    )
    evidence_service = EvidenceService(
        session_factory=fake_session,
        evidence_repo_factory=lambda _s: evidence_repo,
        chunk_set_repo_factory=lambda _s: chunk_set_repo,
    )
    return RagAnswerExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        conversation_repo_factory=lambda _s: conversation_repo,
        message_repo_factory=lambda _s: message_repo,
        claim_set_repo_factory=lambda _s: claim_set_repo,
        attempt_repo_factory=lambda _s: attempt_repo,
        outbox_repo_factory=lambda _s: outbox_repo,
        retriever=retriever,
        evidence_service=evidence_service,
        model_gateway=gateway,
    )


def _make_service(
    executor: RagAnswerExecutor, run_repo, event_repo, attempt_repo, outbox_repo
) -> RunExecutionService:
    """构建接入真实执行器的 RunExecutionService。"""
    return RunExecutionService(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        attempt_repo_factory=lambda _s: attempt_repo,
        outbox_repo_factory=lambda _s: outbox_repo,
        executor=executor.execute,
        worker_id="test-worker:1",
        heartbeat_interval_seconds=3600.0,
    )


async def _add_rag_answer_run(
    run_repo, event_repo, conversation_repo, message_repo, outbox_repo,
    *,
    with_user_message: bool = True,
) -> Run:
    """模拟 post_message 提交并派发后的 rag_answer Run。

    包含 Conversation（已认领活跃 Run）、User Message、run_created
    Event 与已投递 Outbox；Run 处于 QUEUED。
    """
    conversation = create_conversation(
        project_id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        title=None,
        scope_mode=ScopeMode.PROJECT,
    )
    await conversation_repo.add(conversation)
    user_message = create_message(
        conversation_id=conversation.conversation_id,
        sequence=1,
        role=MessageRole.USER,
        content="什么是 RAG？",
    )
    run = create_run(
        project_id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        run_type=RunType.RAG_ANSWER,
        input_payload={
            "conversation_id": conversation.conversation_id,
            "user_message_id": user_message.message_id,
            RUN_INPUT_VERSION_SCOPE_KEY: [
                {"paper_id": _PAPER_ID, "version_id": _VERSION_ID}
            ],
        },
    )
    await run_repo.add(run)
    if with_user_message:
        await message_repo.add(replace(user_message, run_id=run.run_id))
    claimed = await conversation_repo.try_claim_active_run(
        conversation.conversation_id, run.run_id
    )
    assert claimed
    await event_repo.add(
        create_event(
            run_id=run.run_id,
            sequence=1,
            event_type="run_created",
            actor_type="user",
            correlation_id="post-message",
            payload={},
        )
    )
    await run_repo.update_status(run.run_id, RunStatus.QUEUED, RunStatus.QUEUED, 2)
    entry = create_outbox_entry(run.run_id)
    await outbox_repo.add(entry)
    await outbox_repo.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    return loaded


async def _seed_retrieval(chunk_set_repo, chunk_repo) -> None:
    """预设一条快照内检索命中：ready ChunkSet + 一个 Chunk。"""
    chunk_set = create_chunk_set(_REVISION_ID, "profile-hash", {})
    chunk_set = chunk_set.mark_ready(datetime.now(UTC))
    await chunk_set_repo.add(chunk_set)
    chunk = Chunk(
        chunk_id="chunk-1",
        chunk_set_id=chunk_set.chunk_set_id,
        sequence=1,
        text="RAG 将检索与生成结合。",
        token_count=10,
        section_path="1",
        page_start=1,
        page_end=2,
    )
    chunk_repo.semantic_results = [
        RetrievedChunk(chunk=chunk, paper_id=_PAPER_ID, version_id=_VERSION_ID)
    ]


def _events(event_repo: FakeEventRepository, run_id: str) -> list:
    """按 sequence 返回指定 Run 的事件。"""
    return sorted(
        [e for e in event_repo._events if e.run_id == run_id],
        key=lambda e: e.sequence,
    )


def _event_types(event_repo: FakeEventRepository, run_id: str) -> list[str]:
    """按 sequence 返回指定 Run 的事件类型。"""
    return [e.event_type for e in _events(event_repo, run_id)]


async def _conversation_of(conversation_repo, run: Run):
    """按 Run 输入返回会话（测试辅助）。"""
    conversation = await conversation_repo.get_by_id(run.input_payload["conversation_id"])
    assert conversation is not None
    return conversation


async def test_answered_full_pipeline(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """完整闭环：QUEUED → SUCCEEDED，引用齐全、事件序列正确、活跃认领清除。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED

    assert _event_types(event_repo, run.run_id) == [
        "run_created",
        "run_started",
        "retrieval_started",
        "retrieval_completed",
        "model_generation_started",
        "model_generation_completed",
        "citation_validation_completed",
        "answer_committed",
    ]
    retrieval_completed = _events(event_repo, run.run_id)[3]
    assert retrieval_completed.payload["candidate_count"] == 1
    validation_event = _events(event_repo, run.run_id)[6]
    assert validation_event.payload["passed"] is True

    # Evidence 已固化且归属本次 Run
    evidence = await evidence_repo.list_by_run(run.run_id)
    assert len(evidence) == 1
    assert evidence[0].paper_id == _PAPER_ID
    assert evidence[0].version_id == _VERSION_ID
    assert evidence[0].parse_revision_id == _REVISION_ID

    # ClaimSet/Claim/Citation 与 Assistant Message
    claim_set = await claim_set_repo.get_by_run_id(run.run_id)
    assert claim_set is not None
    assert claim_set.answer_status == AnswerStatus.ANSWERED
    claims = await claim_set_repo.list_claims(claim_set.claim_set_id)
    assert len(claims) == 1
    citations = await claim_set_repo.list_citations(claims[0].claim_id)
    assert [c.evidence_id for c in citations] == [evidence[0].evidence_id]
    committed = _events(event_repo, run.run_id)[-1]
    assert committed.payload["claim_set_id"] == claim_set.claim_set_id
    assert committed.payload["answer_status"] == "answered"
    assert committed.payload["claim_count"] == 1

    conversation = await _conversation_of(conversation_repo, run)
    assistant = await message_repo.get_by_run_and_role(run.run_id, MessageRole.ASSISTANT)
    assert assistant is not None
    assert assistant.conversation_id == conversation.conversation_id
    assert assistant.sequence == 2
    assert assistant.claim_set_id == claim_set.claim_set_id
    assert claims[0].text in assistant.content

    # 终态同事务清理活跃认领，会话可继续提问
    assert conversation.active_run_id is None

    # 查询向量与回答生成两次模型调用均记录 run_id
    records = invocation_repo.all()
    assert len(records) == 2
    assert all(record.run_id == run.run_id for record in records)
    assert all(record.status == InvocationStatus.SUCCEEDED for record in records)


async def test_zero_results_commit_insufficient_without_model(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """零候选：不调 Chat 模型，直接提交证据不足回答（业务成功路径）。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    chat_model = _ScriptedChatModel()
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED
    assert chat_model.calls == []
    # 只有查询向量一次模型调用
    assert len(invocation_repo.all()) == 1
    assert "model_generation_started" not in _event_types(event_repo, run.run_id)

    claim_set = await claim_set_repo.get_by_run_id(run.run_id)
    assert claim_set is not None
    assert claim_set.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assistant = await message_repo.get_by_run_and_role(run.run_id, MessageRole.ASSISTANT)
    assert assistant is not None
    assert assistant.content == INSUFFICIENT_EVIDENCE_TEXT
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id is None


async def test_model_returns_insufficient(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """模型明确回答证据不足：合法输出，Run SUCCEEDED，无 Citation。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    chat_model = _ScriptedChatModel(
        responses=['{"answer_status": "insufficient_evidence", "claims": []}']
    )
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    claim_set = await claim_set_repo.get_by_run_id(run.run_id)
    assert claim_set is not None
    assert claim_set.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert await claim_set_repo.list_claims(claim_set.claim_set_id) == []
    assistant = await message_repo.get_by_run_and_role(run.run_id, MessageRole.ASSISTANT)
    assert assistant is not None
    assert assistant.content == INSUFFICIENT_EVIDENCE_TEXT
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id is None


async def test_parse_failure_repaired_once(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """首次输出非法 JSON：追加失败反馈修复重试一次后成功。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    chat_model = _ScriptedChatModel(responses=["这不是 JSON"])
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED
    # 两次 Chat 调用：首次非法 + 修复重试（反馈消息带失败原因）
    assert len(chat_model.calls) == 2
    repair_messages = chat_model.calls[1]
    assert any(
        m.role == "user" and "上次输出未通过校验" in m.content
        for m in repair_messages
    )
    claim_set = await claim_set_repo.get_by_run_id(run.run_id)
    assert claim_set is not None
    assert claim_set.answer_status == AnswerStatus.ANSWERED
    records = invocation_repo.all()
    assert len([r for r in records if r.status == InvocationStatus.SUCCEEDED]) == 3


async def test_validation_failure_repaired(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """合法 JSON 但 Claim 未引用证据：校验失败修复重试后成功。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    chat_model = _ScriptedChatModel(
        responses=[
            '{"answer_status": "answered", '
            '"claims": [{"text": "没有引用的论述", "evidence_ids": []}]}'
        ]
    )
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    assert len(chat_model.calls) == 2
    assert any(
        m.role == "user" and "uncited_claim" in m.content for m in chat_model.calls[1]
    )
    claim_set = await claim_set_repo.get_by_run_id(run.run_id)
    assert claim_set is not None
    assert claim_set.answer_status == AnswerStatus.ANSWERED


async def test_invalid_output_twice_fails(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """修复重试后仍非法：FAILED（model_output_invalid），不产生 Assistant Message。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    chat_model = _ScriptedChatModel(responses=["坏输出", "仍然坏"])
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.FAILED
    assert len(chat_model.calls) == 2
    event_types = _event_types(event_repo, run.run_id)
    validation_events = [
        e for e in _events(event_repo, run.run_id)
        if e.event_type == "citation_validation_completed"
    ]
    assert validation_events[0].payload["passed"] is False
    assert validation_events[0].payload["failure_reasons"] == {"parse_error": 1}
    failed = [e for e in _events(event_repo, run.run_id) if e.event_type == "run_failed"]
    assert failed[0].payload["error"]["type"] == "ModelOutputInvalidError"
    assert "answer_committed" not in event_types
    # 无回答产物；FAILED 终态清理活跃认领
    assert await claim_set_repo.get_by_run_id(run.run_id) is None
    assert (
        await message_repo.get_by_run_and_role(run.run_id, MessageRole.ASSISTANT)
        is None
    )
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id is None


async def test_temporary_chat_error_retries_and_keeps_claim(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """Chat 临时错误（限流）：Run RETRY_WAIT，活跃认领保留（会话仍忙）。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    chat_model = _ScriptedChatModel(responses=[ModelRateLimitError("限流")])
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.RETRY_SCHEDULED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.RETRY_WAIT
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.PENDING
    # RETRY_WAIT 不清认领：Run 未结束，会话仍忙
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id == run.run_id
    records = invocation_repo.all()
    chat_records = [r for r in records if r.status == InvocationStatus.FAILED]
    assert len(chat_records) == 1
    assert chat_records[0].error_type == "ModelRateLimitError"


async def test_permanent_chat_error_fails_and_releases_claim(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """Chat 永久错误（认证失败）：Run FAILED 且清理活跃认领，不重试。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    chat_model = _ScriptedChatModel(responses=[ModelAuthError("缺少 API Key")])
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.FAILED
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id is None


async def test_cancel_after_retrieval_skips_model_call(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """检索期间收到取消请求：Retrieval 后检查命中，CANCELLED 且不调 Chat。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)

    def _cancel_on_first_embed(call_index: int) -> None:
        """查询向量生成时模拟 API 并发写入 CANCEL_REQUESTED。"""
        loaded = run_repo._runs[run.run_id]
        run_repo._runs[run.run_id] = replace(
            loaded, status=RunStatus.CANCEL_REQUESTED
        )

    embedding_model = _ScriptedEmbeddingModel(on_call=_cancel_on_first_embed)
    chat_model = _ScriptedChatModel()
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.SKIPPED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.CANCELLED
    assert chat_model.calls == []
    event_types = _event_types(event_repo, run.run_id)
    assert event_types == [
        "run_created", "run_started", "retrieval_started", "run_cancelled"
    ]
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id is None


async def test_cancel_after_model_call_skips_commit(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """模型调用后收到取消请求：提交前检查命中，CANCELLED 且不提交回答。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)

    def _cancel_on_first_chat(call_index: int) -> None:
        """首次 Chat 调用时模拟 API 并发写入 CANCEL_REQUESTED。"""
        loaded = run_repo._runs[run.run_id]
        run_repo._runs[run.run_id] = replace(
            loaded, status=RunStatus.CANCEL_REQUESTED
        )

    chat_model = _ScriptedChatModel(on_call=_cancel_on_first_chat)
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
        chat_model=chat_model,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.SKIPPED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.CANCELLED
    assert len(chat_model.calls) == 1
    event_types = _event_types(event_repo, run.run_id)
    assert "run_cancelled" in event_types
    assert "answer_committed" not in event_types
    assert await claim_set_repo.get_by_run_id(run.run_id) is None
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id is None


async def test_existing_claim_set_completes_idempotently(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """重复执行兜底：已有 ClaimSet 与 Assistant Message 时回读幂等完成。"""
    run = await _add_rag_answer_run(
        run_repo, event_repo, conversation_repo, message_repo, outbox_repo
    )
    await _seed_retrieval(chunk_set_repo, chunk_repo)
    conversation = await _conversation_of(conversation_repo, run)
    # 模拟上次执行已提交回答产物但 Run 未推进终态（Worker 崩溃）
    existing_claim_set = create_claim_set(run.run_id, AnswerStatus.ANSWERED)
    await claim_set_repo.add_claim_set(existing_claim_set)
    await claim_set_repo.add_claims(
        [create_claim(existing_claim_set.claim_set_id, 1, "既有回答段落。")]
    )
    await message_repo.add(
        create_message(
            conversation_id=conversation.conversation_id,
            sequence=2,
            role=MessageRole.ASSISTANT,
            content="既有回答段落。",
            run_id=run.run_id,
            claim_set_id=existing_claim_set.claim_set_id,
        )
    )
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED
    # 不重复创建 Message/ClaimSet
    messages = await message_repo.list_by_conversation(conversation.conversation_id)
    assert len(messages) == 2
    assert len(claim_set_repo._claim_sets) == 1
    committed = _events(event_repo, run.run_id)[-1]
    assert committed.event_type == "answer_committed"
    assert committed.payload["claim_set_id"] == existing_claim_set.claim_set_id
    assert committed.payload["claim_count"] == 1
    conversation = await _conversation_of(conversation_repo, run)
    assert conversation.active_run_id is None


async def test_defense_rejects_non_rag_answer_run(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """防御：RagAnswerExecutor 收到非 rag_answer 类型直接抛错。"""
    run = create_run(
        project_id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        run_type=RunType.INGESTION,
        input_payload={"parse_revision_id": "rev-1"},
    )
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
    )

    with pytest.raises(ValueError, match="非 rag_answer"):
        await executor.execute(run, correlation_id="job-1")


async def test_missing_conversation_id_fails_permanently(
    run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
    evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    invocation_repo,
) -> None:
    """Run 输入缺 conversation_id：永久输入错误，直接 FAILED 不重试。"""
    run = create_run(
        project_id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        run_type=RunType.RAG_ANSWER,
        input_payload={
            "user_message_id": "m-1",
            RUN_INPUT_VERSION_SCOPE_KEY: [
                {"paper_id": _PAPER_ID, "version_id": _VERSION_ID}
            ],
        },
    )
    await run_repo.add(run)
    await run_repo.update_status(run.run_id, RunStatus.QUEUED, RunStatus.QUEUED, 1)
    entry = create_outbox_entry(run.run_id)
    await outbox_repo.add(entry)
    await outbox_repo.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))
    executor = _make_executor(
        run_repo, event_repo, conversation_repo, message_repo, claim_set_repo,
        evidence_repo, chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
        invocation_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.FAILED
    failed = [e for e in _events(event_repo, run.run_id) if e.event_type == "run_failed"]
    assert failed[0].payload["error"]["type"] == "RagAnswerInputError"
