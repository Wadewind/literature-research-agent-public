"""Queue/Worker 端到端集成测试：ARQ + Valkey + PostgreSQL。

验证切片 5 的最小闭环：
- Outbox 派发器把 run_id 投递到真实 ARQ/Valkey；
- 真实 ARQ Worker（burst 模式）执行 Job 并推进 Run 状态；
- 队列不可用时记录失败并退避，恢复后可补投；
- 相同 Job ID 的重复投递被队列去重。
"""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from arq.connections import RedisSettings, create_pool
from arq.worker import Worker
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.redis import RedisContainer

from literature_agent.application.conversation_service import ConversationService
from literature_agent.application.document_query_service import DocumentQueryService
from literature_agent.application.evidence_service import EvidenceService
from literature_agent.application.indexing_executor import IndexingExecutor
from literature_agent.application.ingestion_executor import IngestionExecutor
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.outbox_dispatch_service import OutboxDispatchService
from literature_agent.application.rag_answer_executor import RagAnswerExecutor
from literature_agent.application.retriever import Retriever
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import RunExecutionService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.domain.tokenization import OFFLINE_TOKENIZER
from literature_agent.infrastructure.models.fake_models import (
    FakeChatModel,
    FakeEmbeddingModel,
)
from literature_agent.infrastructure.parsing.fake_parser import (
    PARSER_NAME,
    PARSER_VERSION,
    FakeDocumentParser,
)
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.conversation_repository import (
    SqlalchemyConversationRepository,
)
from literature_agent.infrastructure.persistence.element_repository import (
    SqlalchemyElementRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.message_repository import (
    SqlalchemyMessageRepository,
)
from literature_agent.infrastructure.persistence.model_invocation_repository import (
    SqlalchemyModelInvocationRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
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
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from literature_agent.infrastructure.queue.arq_run_queue import ArqRunQueue
from literature_agent.worker import execute_run


@pytest_asyncio.fixture
async def valkey_url():
    """启动 Testcontainers Valkey 并返回连接串。"""
    with RedisContainer("valkey/valkey:9") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def queued_run(db_engine) -> str:
    """创建 Project/Paper/Version、QUEUED Run 和 Outbox 记录，返回 run_id。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project(owner_id="user-1", name="测试项目", description="")
        await SqlalchemyProjectRepository(session).add(project)
        # UOW 不感知表级 FK 的插入顺序，逐层 flush
        await session.flush()
        paper = create_paper(owner_id="user-1")
        await SqlalchemyPaperRepository(session).add(paper)
        await session.flush()
        version = create_paper_version(
            paper_id=paper.paper_id,
            owner_id="user-1",
            file_hash="b" * 64,
            storage_key="user-1/proj/paper/paper.pdf",
            size_bytes=100,
            content_type="application/pdf",
        )
        await SqlalchemyPaperVersionRepository(session).add(version)
        await session.flush()
        # 收录关系到 Project（index-status 授权链需要）
        await SqlalchemyProjectPaperRepository(session).add(
            create_project_paper(project.project_id, paper.paper_id, version.version_id)
        )
        run = create_run(
            project_id=project.project_id,
            owner_id="user-1",
            run_type="ingestion",
            input_payload={"paper_id": paper.paper_id, "version_id": version.version_id},
        )
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        await SqlalchemyOutboxRepository(session).add(create_outbox_entry(run.run_id))
        await session.commit()
        return run.run_id


def _session_factory(db_engine):
    """返回基于测试引擎的会话工厂。"""
    return async_sessionmaker(db_engine, expire_on_commit=False)


def _make_execution_service(session_factory) -> RunExecutionService:
    """构建与 Worker 进程一致的真实执行链路（Fake Parser）。"""
    return RunExecutionService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        executor=_make_dispatcher(session_factory).execute,
        worker_id="test-worker:integration",
        heartbeat_interval_seconds=3600.0,
    )


def _make_dispatcher(session_factory) -> RunDispatcher:
    """构建 ingestion + indexing + rag_answer 的组合分发器（与 Worker 装配一致）。"""
    model_gateway = ModelGateway(
        embedding_model=FakeEmbeddingModel(),
        chat_model=FakeChatModel(),
        session_factory=session_factory,
        invocation_repo_factory=SqlalchemyModelInvocationRepository,
    )
    ingestion = IngestionExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        element_repo_factory=SqlalchemyElementRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        parser=FakeDocumentParser(),
        profile=ParseProfile(PARSER_NAME, PARSER_VERSION, {}),
    )
    indexing = IndexingExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        element_repo_factory=SqlalchemyElementRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        chunk_repo_factory=SqlalchemyChunkRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        profile=ChunkProfile(
            embedding_provider="fake",
            embedding_model="fake-embedding",
            embedding_dimensions=1024,
            tokenizer=OFFLINE_TOKENIZER,
        ),
        model_gateway=model_gateway,
        embedding_batch_size=2,
    )
    retriever = Retriever(
        session_factory=session_factory,
        chunk_repo_factory=SqlalchemyChunkRepository,
        model_gateway=model_gateway,
    )
    evidence_service = EvidenceService(
        session_factory=session_factory,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
    )
    rag_answer = RagAnswerExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        conversation_repo_factory=SqlalchemyConversationRepository,
        message_repo_factory=SqlalchemyMessageRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        retriever=retriever,
        evidence_service=evidence_service,
        model_gateway=model_gateway,
        tokenizer=OFFLINE_TOKENIZER,
    )
    return RunDispatcher(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        executors={
            RunType.INGESTION: ingestion.execute,
            RunType.INDEXING: indexing.execute,
            RunType.RAG_ANSWER: rag_answer.execute,
        },
    )


async def test_dispatch_to_worker_completes_run(
    db_engine, valkey_url: str, queued_run: str
) -> None:
    """完整闭环：Outbox → ARQ → Worker → 解析产物与 Run SUCCEEDED。"""
    queue = ArqRunQueue(valkey_url)
    session_factory = _session_factory(db_engine)
    dispatch_service = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=queue,
        max_attempts=10,
        batch_size=20,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )

    dispatched = await dispatch_service.dispatch_pending()
    assert dispatched == 1

    # 重复派发同一 run_id：Outbox 已标记，不会重复投递
    assert await dispatch_service.dispatch_pending() == 0

    worker = Worker(
        redis_settings=RedisSettings.from_dsn(valkey_url),
        functions=[execute_run],
        burst=True,
        handle_signals=False,
        max_tries=1,
        ctx={"run_execution_service": _make_execution_service(session_factory)},
    )
    await worker.async_run()

    async with session_factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(queued_run)
        assert run is not None
        assert run.status == RunStatus.SUCCEEDED
        events = await SqlalchemyEventRepository(session).list_by_run(queued_run)
        assert [e.event_type for e in events] == [
            "run_started",
            "parse_started",
            "parse_completed",
            "normalize_completed",
            "result_committed",
        ]
        # Attempt 运维记录：创建并以 succeeded 关闭
        attempt = await SqlalchemyAttemptRepository(session).get_latest_by_run(queued_run)
        assert attempt is not None
        assert attempt.attempt_number == 1
        assert attempt.worker_id == "test-worker:integration"
        assert attempt.status.value == "succeeded"
        assert attempt.finished_at is not None
        # 解析产物：Revision、Element、当前指针
        version_id = run.input_payload["version_id"]
        version = await SqlalchemyPaperVersionRepository(session).get_by_id(version_id)
        assert version is not None
        assert version.current_parse_revision_id is not None
        revision = await SqlalchemyParseRevisionRepository(session).get_by_id(
            version.current_parse_revision_id
        )
        assert revision is not None
        assert revision.status.value == "succeeded"
        assert (
            await SqlalchemyElementRepository(session).count_by_revision(
                revision.revision_id
            )
            == 8
        )

    await queue.aclose()


async def test_dispatch_failure_then_recovers(db_engine, valkey_url: str, queued_run: str) -> None:
    """队列不可用时记录失败并退避；队列恢复后补投成功。"""
    # 指向不可达地址，模拟 Valkey 故障
    broken_queue = ArqRunQueue("redis://127.0.0.1:6399/0")
    session_factory = _session_factory(db_engine)
    dispatch_service = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=broken_queue,
        max_attempts=10,
        batch_size=20,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )

    assert await dispatch_service.dispatch_pending() == 0
    async with session_factory() as session:
        entry = await SqlalchemyOutboxRepository(session).get_by_run_id(queued_run)
        assert entry is not None
        assert entry.status == OutboxStatus.PENDING
        assert entry.attempt_count == 1

    # “队列恢复”后补投成功（退避窗口内注入 now 快进）
    recovered = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=ArqRunQueue(valkey_url),
        max_attempts=10,
        batch_size=20,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )
    future = datetime.now(UTC) + timedelta(seconds=5)
    assert await recovered.dispatch_pending(future) == 1
    async with session_factory() as session:
        entry = await SqlalchemyOutboxRepository(session).get_by_run_id(queued_run)
        assert entry is not None
        assert entry.status == OutboxStatus.DISPATCHED


async def test_duplicate_enqueue_deduplicated_by_job_id(valkey_url: str) -> None:
    """相同 Job ID 的重复投递在 ARQ 侧去重。"""
    pool = await create_pool(RedisSettings.from_dsn(valkey_url))
    try:
        job1 = await pool.enqueue_job("execute_run", "run-x", _job_id="run:run-x")
        job2 = await pool.enqueue_job("execute_run", "run-x", _job_id="run:run-x")
        assert job1 is not None
        assert job2 is None
    finally:
        await pool.aclose()


async def test_ingestion_then_indexing_completes_end_to_end(
    db_engine, valkey_url: str, queued_run: str
) -> None:
    """端到端：ingestion SUCCEEDED 后自动触发 indexing，ChunkSet ready、Chunk 可查。"""
    queue = ArqRunQueue(valkey_url)
    session_factory = _session_factory(db_engine)
    dispatch_service = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=queue,
        max_attempts=10,
        batch_size=20,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )

    async def _run_worker_once() -> None:
        """以 burst 模式跑一轮 Worker（与 Worker 进程相同的分发装配）。"""
        worker = Worker(
            redis_settings=RedisSettings.from_dsn(valkey_url),
            functions=[execute_run],
            burst=True,
            handle_signals=False,
            max_tries=1,
            ctx={"run_execution_service": _make_execution_service(session_factory)},
        )
        await worker.async_run()

    # 第一轮：ingestion
    assert await dispatch_service.dispatch_pending() == 1
    await _run_worker_once()

    # ingestion 成功后在同事务创建了 indexing Run + Outbox
    indexing_run_id: str | None = None
    async with session_factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        ingestion_run = await run_repo.get_by_id(queued_run)
        assert ingestion_run is not None
        assert ingestion_run.status == RunStatus.SUCCEEDED
        # 找到自动创建的 indexing Run（通过 Outbox 记录定位）
        outbox_repo = SqlalchemyOutboxRepository(session)
        due = await outbox_repo.list_due_pending(datetime.now(UTC), limit=10)
        assert len(due) == 1
        indexing_run_id = due[0].run_id
        indexing_run = await run_repo.get_by_id(indexing_run_id)
        assert indexing_run is not None
        assert indexing_run.run_type == RunType.INDEXING.value
        assert indexing_run.project_id == ingestion_run.project_id
        assert indexing_run.owner_id == ingestion_run.owner_id

    # 第二轮：indexing
    assert await dispatch_service.dispatch_pending() == 1
    await _run_worker_once()

    async with session_factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        indexing_run = await run_repo.get_by_id(indexing_run_id)
        assert indexing_run is not None
        assert indexing_run.status == RunStatus.SUCCEEDED
        events = await SqlalchemyEventRepository(session).list_by_run(indexing_run_id)
        assert [e.event_type for e in events] == [
            "run_created",
            "run_started",
            "indexing_started",
            "chunking_completed",
            "embedding_completed",
            "indexing_completed",
        ]
        # ChunkSet ready、Chunk 与 Element 映射可查
        revision_id = indexing_run.input_payload["parse_revision_id"]
        # profile hash 由执行器使用的 ChunkProfile 决定，按 revision 反查
        chunk_repo = SqlalchemyChunkRepository(session)
        from sqlalchemy import select

        from literature_agent.infrastructure.persistence.models import (
            ChunkORM,
            ChunkSetORM,
        )

        result = await session.execute(
            select(ChunkSetORM).where(ChunkSetORM.parse_revision_id == revision_id)
        )
        row = result.scalar_one()
        assert row.status == "ready"
        chunks = await chunk_repo.list_by_chunk_set(row.chunk_set_id)
        assert len(chunks) > 0
        links = await chunk_repo.list_links([c.chunk_id for c in chunks])
        assert links
        assert any("准确率" in c.text for c in chunks)
        # 切片 5：全部 Chunk 已写回 1024 维向量，search_vector 生成列已派生
        assert all(
            c.embedding is not None and len(c.embedding) == 1024 for c in chunks
        )
        orm_rows = (
            (
                await session.execute(
                    select(ChunkORM).where(ChunkORM.chunk_set_id == row.chunk_set_id)
                )
            )
            .scalars()
            .all()
        )
        assert all(r.search_vector is not None for r in orm_rows)
        # 调用记录持久化且携带 indexing run_id
        invocations = await SqlalchemyModelInvocationRepository(session).list_by_run(
            indexing_run_id
        )
        assert invocations
        assert all(i.capability.value == "embedding" for i in invocations)

        # index-status：授权链走通，chunk_set 计数与 indexing_run_id 正确
        query_service = DocumentQueryService(
            session_factory=session_factory,
            project_repo_factory=SqlalchemyProjectRepository,
            paper_repo_factory=SqlalchemyPaperRepository,
            paper_version_repo_factory=SqlalchemyPaperVersionRepository,
            project_paper_repo_factory=SqlalchemyProjectPaperRepository,
            parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
            element_repo_factory=SqlalchemyElementRepository,
            chunk_set_repo_factory=SqlalchemyChunkSetRepository,
            chunk_repo_factory=SqlalchemyChunkRepository,
            run_repo_factory=SqlalchemyRunRepository,
        )
        index_status = await query_service.get_index_status(
            ActorContext(owner_id="user-1"),
            indexing_run.project_id,
            indexing_run.input_payload["version_id"],
        )
        assert index_status.revision_id == revision_id
        assert index_status.chunk_set is not None
        assert index_status.chunk_set.chunk_set_id == row.chunk_set_id
        assert index_status.chunk_set.status == "ready"
        assert index_status.chunk_set.chunk_count == len(chunks)
        assert index_status.chunk_set.embedded_count == len(chunks)
        assert index_status.indexing_run_id == indexing_run_id

    await queue.aclose()


async def test_rag_answer_completes_end_to_end(
    db_engine, valkey_url: str, queued_run: str
) -> None:
    """端到端第三轮（切片 8）：提问 → rag_answer Run → 带引用的 Assistant Message。

    复用 ingestion → indexing 两轮派发完成索引，然后经真实
    ConversationService 提交提问（Outbox → ARQ → Worker → 回答）。
    """
    queue = ArqRunQueue(valkey_url)
    session_factory = _session_factory(db_engine)
    dispatch_service = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=queue,
        max_attempts=10,
        batch_size=20,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )

    async def _run_worker_once() -> None:
        """以 burst 模式跑一轮 Worker（与 Worker 进程相同的分发装配）。"""
        worker = Worker(
            redis_settings=RedisSettings.from_dsn(valkey_url),
            functions=[execute_run],
            burst=True,
            handle_signals=False,
            max_tries=1,
            ctx={"run_execution_service": _make_execution_service(session_factory)},
        )
        await worker.async_run()

    # 前两轮：ingestion → indexing，文献进入可检索状态
    assert await dispatch_service.dispatch_pending() == 1
    await _run_worker_once()
    assert await dispatch_service.dispatch_pending() == 1
    await _run_worker_once()

    async with session_factory() as session:
        ingestion_run = await SqlalchemyRunRepository(session).get_by_id(queued_run)
        assert ingestion_run is not None
        project_id = ingestion_run.project_id

    # 经真实 ConversationService 创建会话并提交提问
    conversation_service = ConversationService(
        session_factory=session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        conversation_repo_factory=SqlalchemyConversationRepository,
        message_repo_factory=SqlalchemyMessageRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
    )
    actor = ActorContext(owner_id="user-1")
    view = await conversation_service.create_conversation(
        actor, project_id, title=None, scope_mode="project", paper_ids=None
    )
    conversation_id = view.conversation.conversation_id
    # 问题包含英文词 "fake"：与 Fake Parser 产出的标题 Chunk 词汇重叠，
    # 保证确定性 bag-of-words 向量检索必有命中
    posted = await conversation_service.post_message(
        actor,
        conversation_id,
        content="fake 论文讲了什么？",
        idempotency_key="e2e-key-1",
        correlation_id="e2e-post",
    )
    assert posted.status == "queued"

    # 第三轮：rag_answer
    assert await dispatch_service.dispatch_pending() == 1
    await _run_worker_once()

    async with session_factory() as session:
        run_repo = SqlalchemyRunRepository(session)
        run = await run_repo.get_by_id(posted.run_id)
        assert run is not None
        assert run.status == RunStatus.SUCCEEDED
        assert run.run_type == RunType.RAG_ANSWER.value

        events = await SqlalchemyEventRepository(session).list_by_run(posted.run_id)
        assert [e.event_type for e in events] == [
            "run_created",
            "run_started",
            "retrieval_started",
            "retrieval_completed",
            "model_generation_started",
            "model_generation_completed",
            "citation_validation_completed",
            "answer_committed",
        ]

        # 回答产物：Assistant Message + ClaimSet + Claims + Citations
        message_repo = SqlalchemyMessageRepository(session)
        from literature_agent.domain.conversation import MessageRole

        assistant = await message_repo.get_by_run_and_role(
            posted.run_id, MessageRole.ASSISTANT
        )
        assert assistant is not None
        assert assistant.conversation_id == conversation_id
        assert assistant.claim_set_id is not None
        claim_set_repo = SqlalchemyClaimSetRepository(session)
        claims = await claim_set_repo.list_claims(assistant.claim_set_id)
        assert len(claims) == 1
        citations = await claim_set_repo.list_citations(claims[0].claim_id)
        assert citations
        evidence = await SqlalchemyEvidenceRepository(session).list_by_run(
            posted.run_id
        )
        assert evidence
        evidence_ids = {e.evidence_id for e in evidence}
        assert all(c.evidence_id in evidence_ids for c in citations)

        # 模型调用记录：查询向量（embedding）与回答生成（chat）都带 run_id
        invocations = await SqlalchemyModelInvocationRepository(session).list_by_run(
            posted.run_id
        )
        capabilities = {i.capability.value for i in invocations}
        assert capabilities == {"embedding", "chat"}

        # 终态后活跃认领已清理，会话可继续提问
        conversation = await SqlalchemyConversationRepository(session).get_by_id(
            conversation_id
        )
        assert conversation is not None
        assert conversation.active_run_id is None

    await queue.aclose()
