"""ARQ Worker 进程入口。

``python -m literature_agent.worker`` 启动 Worker 进程，包含两部分职责：

- 注册并执行 ``execute_run`` Job，Job 只携带稳定 ``run_id``；
- 运行 Outbox 派发循环，把数据库中到期的 Outbox 记录投递到 ARQ。

业务事实始终以 PostgreSQL 为准；ARQ/Valkey 只承担 Job 投递。
"""

import asyncio
import hashlib
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
    suppress,
)
from typing import Any, cast
from urllib.parse import urlparse

from arq.connections import RedisSettings
from arq.worker import func, run_worker
from deepagents.backends import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from literature_agent.application.agent_turn_executor import AgentTurnExecutor
from literature_agent.application.agent_turn_lifecycle_service import (
    AgentTurnLifecycleService,
)
from literature_agent.application.arxiv_import_service import ArxivProjectImportService
from literature_agent.application.evidence_service import EvidenceService
from literature_agent.application.indexing_executor import IndexingExecutor
from literature_agent.application.ingestion_executor import IngestionExecutor
from literature_agent.application.mcp_tool_execution_service import McpToolExecutionService
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.outbox_dispatch_service import OutboxDispatchService
from literature_agent.application.ports.arxiv_gateway import ArxivGateway
from literature_agent.application.ports.chat_model import ChatModel
from literature_agent.application.ports.document_parser import DocumentParser
from literature_agent.application.ports.embedding_model import EmbeddingModel
from literature_agent.application.ports.event_notifier import EventNotifier
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntime,
    RuntimeTurnRequest,
)
from literature_agent.application.project_research_context_service import (
    ProjectResearchContextService,
)
from literature_agent.application.rag_answer_executor import RagAnswerExecutor
from literature_agent.application.retriever import Retriever
from literature_agent.application.review_dependency_service import (
    ReviewDependencyReconciler,
    ReviewDependencyWaitService,
)
from literature_agent.application.review_evidence_matrix_service import ReviewEvidenceMatrixService
from literature_agent.application.review_executor import ReviewExecutor
from literature_agent.application.review_export_service import ReviewExportService
from literature_agent.application.review_outline_service import (
    ReviewOutlineDecisionService,
    ReviewOutlineService,
)
from literature_agent.application.review_search_strategy_service import ReviewSearchStrategyService
from literature_agent.application.review_section_service import ReviewSectionService
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import RunExecutionService
from literature_agent.application.run_reconcile_service import RunReconcileService
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlService,
)
from literature_agent.application.waiting_run_resume_service import WaitingRunResumeService
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.run import RunType
from literature_agent.domain.tokenization import OFFLINE_TOKENIZER
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    DeepAgentsResearchAgentRuntime,
)
from literature_agent.infrastructure.agent.deepseek_research_model import (
    aclose_deepseek_research_model,
    build_deepseek_research_model,
)
from literature_agent.infrastructure.agent.fake_research_agent_runtime import (
    FakeResearchAgentRuntime,
)
from literature_agent.infrastructure.agent.mcp_tools import (
    LangchainMcpToolLoader,
)
from literature_agent.infrastructure.agent.opensandbox_backend import OpenSandboxProvider
from literature_agent.infrastructure.agent.sandbox_mcp import (
    PLATFORM_SANDBOX_MCP_RESOLVER,
)
from literature_agent.infrastructure.agent.sandbox_workspace import SandboxWorkspaceManager
from literature_agent.infrastructure.agent.sandboxed_research_agent_runtime import (
    CheckpointExecutionFactory,
    SandboxedResearchAgentRuntime,
)
from literature_agent.infrastructure.agent.skill_backend import (
    PlatformSkillMaterializer,
    SkillRuntimeMaterialization,
)
from literature_agent.infrastructure.agent.skill_catalog import PLATFORM_SKILLS
from literature_agent.infrastructure.arxiv import HttpxArxivGateway
from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.fake_arxiv import FixtureArxivGateway
from literature_agent.infrastructure.models.fake_models import (
    EMBEDDING_COLUMN_DIMENSIONS,
    FakeChatModel,
    FakeEmbeddingModel,
)
from literature_agent.infrastructure.models.openai_compatible import (
    OpenAiCompatibleChat,
    OpenAiCompatibleEmbedding,
)
from literature_agent.infrastructure.parsing.fake_parser import (
    FakeDocumentParser,
)
from literature_agent.infrastructure.parsing.fallback_parser import FallbackDocumentParser
from literature_agent.infrastructure.parsing.pypdf_parser import PypdfDocumentParser
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
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
from literature_agent.infrastructure.persistence.database import (
    create_engine,
    create_session_factory,
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
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from literature_agent.infrastructure.persistence.runtime_execution_repository import (
    SqlalchemyRuntimeExecutionRepository,
)
from literature_agent.infrastructure.persistence.sandbox_workspace_repository import (
    SqlalchemySandboxWorkspaceRepository,
    SqlalchemyWorkspaceSnapshotPublisher,
)
from literature_agent.infrastructure.persistence.skill_repository import SqlalchemySkillRepository
from literature_agent.infrastructure.persistence.tool_execution_repository import (
    SqlalchemyToolExecutionRepository,
)
from literature_agent.infrastructure.queue.arq_run_queue import ArqRunQueue
from literature_agent.infrastructure.queue.valkey_event_notifier import (
    ValkeyEventNotifier,
)
from literature_agent.infrastructure.storage.local_storage import LocalFileStorage
from literature_agent.infrastructure.workflow.postgres_checkpoint import (
    PostgresCheckpointPool,
    PostgresCheckpointStore,
)
from literature_agent.metrics import (
    metrics,
    start_worker_metrics_server,
    stop_worker_metrics_server,
)
from literature_agent.observability import (
    bind_log_context,
    configure_logging,
    log_event,
)

logger = logging.getLogger(__name__)


def _build_parser_and_profile(settings: Settings) -> tuple[DocumentParser, ParseProfile]:
    """按 ``parser_backend`` 配置选择 Parser 实现与默认 ParseProfile。

    ``docling``（默认）：Docling 主路径 + pypdf 降级组合，默认不开 OCR；
    ``fake``：确定性 Fake Parser，用于闭环演示与不依赖模型的测试。
    Docling 延迟导入，避免 fake 模式下加载重型依赖。
    """
    if settings.parser_backend == "fake":
        return FakeDocumentParser(), ParseProfile("fake", "1.0", {})
    if settings.parser_backend == "docling":
        from literature_agent.infrastructure.parsing.docling_parser import (
            PARSER_NAME,
            PARSER_VERSION,
            DoclingDocumentParser,
        )

        storage = LocalFileStorage(settings.storage_root)
        parser = FallbackDocumentParser(
            primary=DoclingDocumentParser(storage),
            fallback=PypdfDocumentParser(storage),
        )
        return parser, ParseProfile(PARSER_NAME, PARSER_VERSION, {"ocr_enabled": False})
    raise ValueError(f"未知 parser_backend: {settings.parser_backend}")


def _build_chunk_profile(settings: Settings) -> ChunkProfile:
    """从 Settings 构建当前活动的 ChunkProfile。

    Chunk 参数来自 ``AGENT_CHUNK_*``；Embedding 三元组复用切片 3 的
    ``AGENT_EMBEDDING_*``。Settings 无独立 provider 字段，provider 以
    ``embedding_base_url`` 的主机名标识（base_url 变化即 Provider 变化）。
    """
    return ChunkProfile(
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        embedding_provider=urlparse(settings.embedding_base_url).netloc
        or settings.embedding_base_url,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
    )


def _build_model_stack(
    settings: Settings,
    session_factory: Callable[[], AbstractAsyncContextManager[Any]],
) -> tuple[ModelGateway, ChunkProfile, list[Any]]:
    """按 ``embedding_backend``/``chat_backend`` 装配模型栈与活动 ChunkProfile。

    ``fake``（默认）：确定性 Fake 模型，本地开发与测试默认不触网；
    profile 的 embedding 三元组固定为 fake 标识
    （provider="fake", model="fake-embedding", dimensions=1024），
    避免 fake 产出的 ChunkSet 与真实 profile 混淆。
    ``openai_compatible``：OpenAI 兼容 Adapter；profile 三元组来自
    ``AGENT_EMBEDDING_*``，缺 API Key 时首次调用抛 ModelAuthError。
    Embedding 与 Chat 的 backend 独立开关（切片 8 起），互不影响。

    返回 ``(gateway, profile, closables)``：closables 为需要随 Worker
    关闭释放的 Adapter（Fake 无资源，不在其中）。
    """
    if settings.embedding_backend == "fake":
        embedding_model: EmbeddingModel = FakeEmbeddingModel()
        profile = ChunkProfile(
            max_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            embedding_provider=FakeEmbeddingModel.provider,
            embedding_model=FakeEmbeddingModel.model,
            embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            tokenizer=OFFLINE_TOKENIZER,
        )
        closables: list[Any] = []
    elif settings.embedding_backend == "openai_compatible":
        embedding_model = OpenAiCompatibleEmbedding(
            provider=urlparse(settings.embedding_base_url).netloc
            or settings.embedding_base_url,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            timeout_seconds=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            dimensions=settings.embedding_dimensions,
        )
        profile = _build_chunk_profile(settings)
        closables = [embedding_model]
    else:
        raise ValueError(f"未知 embedding_backend: {settings.embedding_backend}")
    if settings.chat_backend == "fake":
        chat_model: ChatModel = FakeChatModel()
    elif settings.chat_backend == "openai_compatible":
        chat_adapter = OpenAiCompatibleChat(
            provider=urlparse(settings.chat_base_url).netloc or settings.chat_base_url,
            base_url=settings.chat_base_url,
            api_key=settings.chat_api_key,
            model=settings.chat_model,
            timeout_seconds=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            json_schema_supported=settings.chat_json_schema_supported,
        )
        chat_model = chat_adapter
        closables.append(chat_adapter)
    else:
        raise ValueError(f"未知 chat_backend: {settings.chat_backend}")
    gateway = ModelGateway(
        embedding_model=embedding_model,
        chat_model=chat_model,
        session_factory=session_factory,
        invocation_repo_factory=SqlalchemyModelInvocationRepository,
    )
    return gateway, profile, closables


def _build_arxiv_gateway(settings: Settings) -> tuple[ArxivGateway, list[Any]]:
    """按显式 backend 选择离线 Fixture 或真实 HTTP arXiv Adapter。"""
    if settings.arxiv_backend == "fake":
        return FixtureArxivGateway(), []
    if settings.arxiv_backend == "httpx":
        gateway = HttpxArxivGateway(max_file_bytes=settings.max_upload_size_bytes)
        return gateway, [gateway]
    raise ValueError(f"未知 arxiv_backend: {settings.arxiv_backend}")


def _build_research_agent_runtime(
    settings: Settings,
    *,
    session_factory: Callable[[], AbstractAsyncContextManager[Any]],
    retriever: Retriever,
    event_notifier: EventNotifier,
    checkpoint_factory: CheckpointExecutionFactory | None,
    workspace_manager: SandboxWorkspaceManager | None,
    runtime_owner_id: str,
    model: BaseChatModel | None = None,
) -> ResearchAgentRuntime:
    """按显式 Worker 配置装配 SDK-neutral Research Agent Runtime。"""
    if settings.research_runtime_backend == "fake":
        return FakeResearchAgentRuntime()
    if settings.research_runtime_backend != "deep_agents":
        raise ValueError(
            f"未知 research_runtime_backend: {settings.research_runtime_backend}"
        )
    if checkpoint_factory is None or workspace_manager is None:
        raise ValueError("deep_agents 模式缺少 Checkpoint pool 或 Sandbox Workspace")
    if settings.research_model_api_key is None:
        raise ValueError("deep_agents 模式必须设置 AGENT_RESEARCH_MODEL_API_KEY")
    if model is None:
        model = build_deepseek_research_model(
            base_url=settings.research_model_base_url,
            api_key=settings.research_model_api_key,
            model=settings.research_model,
            max_output_tokens=settings.research_model_max_output_tokens,
            timeout_seconds=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
        )
    execution_control = RuntimeExecutionControlService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        execution_repo_factory=SqlalchemyRuntimeExecutionRepository,
        lease_seconds=settings.worker_lease_seconds,
    )
    project_context = ProjectResearchContextService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=cast(Any, retriever),
        chunk_repo_factory=SqlalchemyChunkRepository,
        event_notifier=event_notifier,
    )
    def runtime_factory(
        checkpointer: BaseCheckpointSaver[str],
        backend: BackendProtocol,
        before_succeed: Callable[[RuntimeTurnRequest], Awaitable[None]] | None,
    ) -> ResearchAgentRuntime:
        return DeepAgentsResearchAgentRuntime(
            model=model,
            checkpointer=checkpointer,
            backend=backend,
            project_context=project_context,
            execution_control=execution_control,
            runtime_owner_id=runtime_owner_id,
            before_succeed=before_succeed,
        )

    def runtime_with_tools_factory(
        checkpointer: BaseCheckpointSaver[str],
        backend: BackendProtocol,
        before_succeed: Callable[[RuntimeTurnRequest], Awaitable[None]] | None,
        tools: tuple[Any, ...],
    ) -> ResearchAgentRuntime:
        return DeepAgentsResearchAgentRuntime(
            model=model,
            checkpointer=checkpointer,
            backend=backend,
            tools=tools,
            project_context=project_context,
            execution_control=execution_control,
            runtime_owner_id=runtime_owner_id,
            before_succeed=before_succeed,
        )

    def runtime_with_capabilities_factory(
        checkpointer: BaseCheckpointSaver[str],
        backend: BackendProtocol,
        before_succeed: Callable[[RuntimeTurnRequest], Awaitable[None]] | None,
        tools: tuple[Any, ...],
        skills: SkillRuntimeMaterialization,
    ) -> ResearchAgentRuntime:
        return DeepAgentsResearchAgentRuntime(
            model=model,
            checkpointer=checkpointer,
            backend=backend,
            tools=tools,
            project_context=project_context,
            execution_control=execution_control,
            runtime_owner_id=runtime_owner_id,
            before_succeed=before_succeed,
            skill_backend=skills.backend,
            skill_sources=skills.sources,
        )

    mcp_guard = McpToolExecutionService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        tool_execution_repo_factory=SqlalchemyToolExecutionRepository,
        event_repo_factory=SqlalchemyEventRepository,
        event_notifier=event_notifier,
    )
    mcp_loader = LangchainMcpToolLoader(
        connection_resolver=PLATFORM_SANDBOX_MCP_RESOLVER,
        guard=mcp_guard,
        execution_control=execution_control,
    )
    skill_materializer = PlatformSkillMaterializer(
        session_factory=session_factory,
        skill_repo_factory=SqlalchemySkillRepository,
        platform_skills=PLATFORM_SKILLS,
    )

    return SandboxedResearchAgentRuntime(
        checkpoint_factory=checkpoint_factory,
        runtime_factory=runtime_factory,
        workspace_manager=workspace_manager,
        runtime_with_tools_factory=runtime_with_tools_factory,
        mcp_tool_loader=mcp_loader,
        runtime_with_capabilities_factory=runtime_with_capabilities_factory,
        skill_materializer=skill_materializer,
    )


@asynccontextmanager
async def _open_research_agent_runtime(
    settings: Settings,
    *,
    session_factory: Callable[[], AbstractAsyncContextManager[Any]],
    retriever: Retriever,
    event_notifier: EventNotifier,
    runtime_owner_id: str,
):
    """持有真实模型、Checkpoint pool 与 Sandbox 配置的 Worker 生命周期。"""
    if settings.research_runtime_backend == "fake":
        yield _build_research_agent_runtime(
            settings,
            session_factory=session_factory,
            retriever=retriever,
            event_notifier=event_notifier,
            checkpoint_factory=None,
            workspace_manager=None,
            runtime_owner_id=runtime_owner_id,
        )
        return
    if settings.research_runtime_backend != "deep_agents":
        raise ValueError(
            f"未知 research_runtime_backend: {settings.research_runtime_backend}"
        )
    if settings.research_model_api_key is None:
        raise ValueError("deep_agents 模式必须设置 AGENT_RESEARCH_MODEL_API_KEY")
    model = build_deepseek_research_model(
        base_url=settings.research_model_base_url,
        api_key=settings.research_model_api_key,
        model=settings.research_model,
        max_output_tokens=settings.research_model_max_output_tokens,
        timeout_seconds=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
    )
    workspace_manager = SandboxWorkspaceManager(
        repository=SqlalchemySandboxWorkspaceRepository(session_factory),
        provider=OpenSandboxProvider(
            domain=settings.research_sandbox_domain,
            protocol=settings.research_sandbox_protocol,
            api_key=settings.research_sandbox_api_key,
        ),
        storage=LocalFileStorage(settings.storage_root),
        image_ref=settings.research_sandbox_image,
    )
    try:
        async with PostgresCheckpointPool(
            settings.database_url, min_size=1, max_size=4
        ).open() as checkpoint_factory:
            yield _build_research_agent_runtime(
                settings,
                session_factory=session_factory,
                retriever=retriever,
                event_notifier=event_notifier,
                checkpoint_factory=checkpoint_factory,
                workspace_manager=workspace_manager,
                runtime_owner_id=runtime_owner_id,
                model=model,
            )
    finally:
        await aclose_deepseek_research_model(model)


async def execute_run(ctx: dict[str, Any], run_id: str) -> str:
    """ARQ Job：执行一个 Run，只接收 run_id。

    Job 可安全重复执行：RunExecutionService 只在 QUEUED 状态认领 Run。
    """
    service: RunExecutionService = ctx["run_execution_service"]
    raw_job_id = str(ctx.get("job_id", run_id))
    digest = hashlib.sha256(f"{run_id}:{raw_job_id}".encode()).hexdigest()[:24]
    correlation_id = f"worker:{digest}"
    with (
        metrics.active_worker_job(),
        bind_log_context(service="worker", correlation_id=correlation_id, run_id=run_id),
    ):
        outcome = await service.execute(run_id, correlation_id=correlation_id)
    return outcome.value


async def _dispatch_loop(ctx: dict[str, Any]) -> None:
    """周期性把到期的 Outbox 记录派发到队列。"""
    service: OutboxDispatchService = ctx["outbox_dispatch_service"]
    settings: Settings = ctx["settings"]
    while True:
        try:
            await service.dispatch_pending()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "outbox_dispatch_loop_failed",
                exc=exc,
                error_code=type(exc).__name__,
            )
        await asyncio.sleep(settings.outbox_poll_interval_seconds)


async def _reconcile_loop(ctx: dict[str, Any]) -> None:
    """周期性收回 lease 过期的执行中 Run（Worker 崩溃恢复）。"""
    service: RunReconcileService = ctx["run_reconcile_service"]
    settings: Settings = ctx["settings"]
    while True:
        try:
            recovered = await service.reconcile_expired()
            orphaned = await service.reconcile_orphaned_attempts()
            if recovered:
                log_event(logger, logging.INFO, "run_reconcile_completed", count=recovered)
            if orphaned:
                log_event(
                    logger,
                    logging.INFO,
                    "attempt_reconcile_completed",
                    count=orphaned,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "run_reconcile_loop_failed",
                exc=exc,
                error_code=type(exc).__name__,
            )
        await asyncio.sleep(settings.worker_reconcile_interval_seconds)


async def _dependency_reconcile_loop(ctx: dict[str, Any]) -> None:
    """周期性对账 Review Run 的论文解析与索引依赖。"""
    service: ReviewDependencyReconciler = ctx["review_dependency_reconciler"]
    settings: Settings = ctx["settings"]
    while True:
        try:
            completed = await service.reconcile_waiting()
            if completed:
                log_event(
                    logger,
                    logging.INFO,
                    "review_dependency_reconcile_completed",
                    count=completed,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 与 lease Reconciler 隔离：本循环单轮失败不影响 Worker 崩溃恢复。
            log_event(
                logger,
                logging.ERROR,
                "review_dependency_reconcile_loop_failed",
                exc=exc,
                error_code=type(exc).__name__,
            )
        await asyncio.sleep(settings.worker_reconcile_interval_seconds)


async def _startup(ctx: dict[str, Any], settings: Settings) -> None:
    """Worker 启动：建立数据库、队列依赖并启动派发与对账循环。"""
    configure_logging(service="worker")
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    queue = ArqRunQueue(settings.redis_url)
    event_notifier = ValkeyEventNotifier(settings.redis_url)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["queue"] = queue
    ctx["event_notifier"] = event_notifier
    ctx["outbox_dispatch_service"] = OutboxDispatchService(
        session_factory=session_factory,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        queue=queue,
        max_attempts=settings.outbox_max_attempts,
        batch_size=settings.outbox_dispatch_batch_size,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
    )
    parser, profile = _build_parser_and_profile(settings)
    ingestion_executor = IngestionExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        element_repo_factory=SqlalchemyElementRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        parser=parser,
        profile=profile,
        parser_timeout_seconds=settings.parser_timeout_seconds,
        max_run_attempts=settings.max_run_attempts,
        event_notifier=event_notifier,
    )
    model_gateway, chunk_profile, model_adapters = _build_model_stack(
        settings, session_factory
    )
    ctx["model_adapters"] = model_adapters
    indexing_executor = IndexingExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        element_repo_factory=SqlalchemyElementRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        chunk_repo_factory=SqlalchemyChunkRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        profile=chunk_profile,
        model_gateway=model_gateway,
        embedding_batch_size=settings.embedding_batch_size,
        max_run_attempts=settings.max_run_attempts,
        event_notifier=event_notifier,
    )
    # 组合 dispatcher：按 run_type 显式分发，未知类型显式失败
    retriever = Retriever(
        session_factory=session_factory,
        chunk_repo_factory=SqlalchemyChunkRepository,
        model_gateway=model_gateway,
        top_k=settings.retrieval_top_k,
        per_paper_limit=settings.retrieval_per_paper_limit,
        token_budget=settings.retrieval_token_budget,
    )
    evidence_service = EvidenceService(
        session_factory=session_factory,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
    )
    rag_answer_executor = RagAnswerExecutor(
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
        answer_max_output_tokens=settings.answer_max_output_tokens,
        context_token_budget=settings.retrieval_token_budget,
        max_run_attempts=settings.max_run_attempts,
        event_notifier=event_notifier,
        tokenizer=chunk_profile.tokenizer,
    )
    storage = LocalFileStorage(settings.storage_root)
    arxiv_gateway, arxiv_adapters = _build_arxiv_gateway(settings)
    model_adapters.extend(arxiv_adapters)
    review_repo = SqlalchemyReviewRepository
    strategy_service = ReviewSearchStrategyService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=review_repo,
        event_repo_factory=SqlalchemyEventRepository,
        model_gateway=model_gateway,
        event_notifier=event_notifier,
    )
    arxiv_service = ArxivProjectImportService(
        session_factory=session_factory,
        arxiv_gateway=arxiv_gateway,
        storage=storage,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        review_repo_factory=review_repo,
        total_download_budget_bytes=settings.max_upload_size_bytes * 10,
        event_notifier=event_notifier,
    )
    dependency_wait_service = ReviewDependencyWaitService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        review_repo_factory=review_repo,
        event_notifier=event_notifier,
    )
    matrix_service = ReviewEvidenceMatrixService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=review_repo,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        chunk_repo_factory=SqlalchemyChunkRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        event_repo_factory=SqlalchemyEventRepository,
        retriever=retriever,
        model_gateway=model_gateway,
        event_notifier=event_notifier,
    )
    outline_service = ReviewOutlineService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=review_repo,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        event_repo_factory=SqlalchemyEventRepository,
        model_gateway=model_gateway,
        event_notifier=event_notifier,
    )
    outline_decision = ReviewOutlineDecisionService(
        session_factory=session_factory,
        review_repo_factory=review_repo,
    )
    section_service = ReviewSectionService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=review_repo,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        event_repo_factory=SqlalchemyEventRepository,
        model_gateway=model_gateway,
        event_notifier=event_notifier,
    )
    export_service = ReviewExportService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=review_repo,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        event_repo_factory=SqlalchemyEventRepository,
        model_invocation_repo_factory=SqlalchemyModelInvocationRepository,
        storage=storage,
        event_notifier=event_notifier,
    )
    review_executor = ReviewExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=review_repo,
        strategy_service=strategy_service,
        arxiv_service=arxiv_service,
        dependency_wait_service=dependency_wait_service,
        matrix_service=matrix_service,
        outline_service=outline_service,
        outline_decision_service=outline_decision,
        section_service=section_service,
        export_service=export_service,
        checkpoint_store=PostgresCheckpointStore(settings.database_url),
    )
    agent_runtime_resources = AsyncExitStack()
    try:
        research_agent_runtime = await agent_runtime_resources.enter_async_context(
            _open_research_agent_runtime(
                settings,
                session_factory=session_factory,
                retriever=retriever,
                event_notifier=event_notifier,
                runtime_owner_id=f"agent-runtime:{worker_id}",
            )
        )
    except BaseException:
        await agent_runtime_resources.aclose()
        raise
    ctx["agent_runtime_resources"] = agent_runtime_resources
    ctx["research_agent_runtime"] = research_agent_runtime
    agent_turn_executor = AgentTurnExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        event_repo_factory=SqlalchemyEventRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        runtime=research_agent_runtime,
        event_notifier=event_notifier,
        workspace_snapshot_publisher_factory=SqlalchemyWorkspaceSnapshotPublisher,
        workspace_snapshot_required=settings.research_runtime_backend == "deep_agents",
    )
    agent_turn_lifecycle = AgentTurnLifecycleService(
        session_factory,
        SqlalchemyRunRepository,
        SqlalchemyAgentRepository,
    )
    dispatcher = RunDispatcher(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        executors={
            RunType.INGESTION: ingestion_executor.execute,
            RunType.INDEXING: indexing_executor.execute,
            RunType.RAG_ANSWER: rag_answer_executor.execute,
            RunType.REVIEW: review_executor.execute,
            RunType.AGENT_TURN: agent_turn_executor.execute,
        },
        event_notifier=event_notifier,
    )
    ctx["run_execution_service"] = RunExecutionService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        executor=dispatcher.execute,
        worker_id=worker_id,
        heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
        max_run_attempts=settings.max_run_attempts,
        event_notifier=event_notifier,
        terminal_callback=agent_turn_lifecycle.release_if_terminal,
    )
    ctx["run_reconcile_service"] = RunReconcileService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        lease_seconds=settings.worker_lease_seconds,
        max_run_attempts=settings.max_run_attempts,
        batch_size=settings.outbox_dispatch_batch_size,
        terminal_callback=agent_turn_lifecycle.release_if_terminal,
    )
    waiting_resume_service = WaitingRunResumeService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        event_notifier=event_notifier,
    )
    ctx["review_dependency_reconciler"] = ReviewDependencyReconciler(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        waiting_resume_service=waiting_resume_service,
        batch_size=settings.outbox_dispatch_batch_size,
        event_notifier=event_notifier,
    )
    # Metrics server 最后启动；端口占用只会禁用本进程 scrape，不影响 Worker。
    if "metrics_server" not in ctx:
        ctx["metrics_server"] = start_worker_metrics_server(settings.worker_metrics_port)
    ctx["dispatch_task"] = asyncio.create_task(_dispatch_loop(ctx))
    ctx["reconcile_task"] = asyncio.create_task(_reconcile_loop(ctx))
    ctx["dependency_reconcile_task"] = asyncio.create_task(
        _dependency_reconcile_loop(ctx)
    )


async def _shutdown(ctx: dict[str, Any]) -> None:
    """Worker 关闭：取消后台循环并释放连接资源。"""
    stop_worker_metrics_server(ctx.pop("metrics_server", None))
    for key in ("dispatch_task", "reconcile_task", "dependency_reconcile_task"):
        task: asyncio.Task | None = ctx.get(key)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    agent_runtime_resources: AsyncExitStack | None = ctx.pop(
        "agent_runtime_resources", None
    )
    if agent_runtime_resources is not None:
        await agent_runtime_resources.aclose()
    # 模型 Adapter 的 HTTP 客户端随进程退出前显式关闭
    for adapter in ctx.get("model_adapters", []):
        await adapter.aclose()
    notifier: ValkeyEventNotifier | None = ctx.get("event_notifier")
    if notifier is not None:
        await notifier.aclose()
    queue: ArqRunQueue | None = ctx.get("queue")
    if queue is not None:
        await queue.aclose()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


def make_worker_settings(settings: Settings) -> type:
    """根据配置构造 ARQ WorkerSettings 类。

    参数:
        settings: 应用配置。

    返回:
        可被 ``arq.worker.run_worker`` 或 arq CLI 使用的配置类。
    """

    async def startup(ctx: dict[str, Any]) -> None:
        await _startup(ctx, settings)

    async def shutdown(ctx: dict[str, Any]) -> None:
        await _shutdown(ctx)

    class WorkerSettings:
        """ARQ Worker 配置。

        ``max_tries = 1``：重试由 Outbox 退避和业务层负责，
        ARQ 不叠加自动重试，避免多层重试相乘。
        """

        # 稳定 Job ID 只用于“当前排队/执行中”去重；等待恢复后的合法新 Attempt
        # 必须能够复用同一 ID。ARQ result 不是业务事实，因此不保留。
        functions = [func(execute_run, keep_result=0)]
        on_startup = startup
        on_shutdown = shutdown
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        max_tries = 1
        # ARQ 的 max-tries/反序列化等提前失败路径不会读取函数级配置；
        # Worker 级也禁用 Result，避免旧失败 key 阻塞合法的同 ID 重投。
        keep_result = 0
        # ARQ Job 超时必须大于 Parser 超时，给状态提交留出余量
        job_timeout = int(settings.parser_timeout_seconds) + 60

    return WorkerSettings


def main() -> None:
    """Worker 进程入口。"""
    configure_logging(service="worker")
    settings = Settings.from_env()
    run_worker(make_worker_settings(settings))


if __name__ == "__main__":
    main()
