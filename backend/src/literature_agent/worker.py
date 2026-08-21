"""ARQ Worker 进程入口。

``python -m literature_agent.worker`` 启动 Worker 进程，包含两部分职责：

- 注册并执行 ``execute_run`` Job，Job 只携带稳定 ``run_id``；
- 运行 Outbox 派发循环，把数据库中到期的 Outbox 记录投递到 ARQ。

业务事实始终以 PostgreSQL 为准；ARQ/Valkey 只承担 Job 投递。
"""

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Any
from urllib.parse import urlparse

from arq.connections import RedisSettings
from arq.worker import run_worker

from literature_agent.application.evidence_service import EvidenceService
from literature_agent.application.indexing_executor import IndexingExecutor
from literature_agent.application.ingestion_executor import IngestionExecutor
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.outbox_dispatch_service import OutboxDispatchService
from literature_agent.application.ports.chat_model import ChatModel
from literature_agent.application.ports.document_parser import DocumentParser
from literature_agent.application.ports.embedding_model import EmbeddingModel
from literature_agent.application.rag_answer_executor import RagAnswerExecutor
from literature_agent.application.retriever import Retriever
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import RunExecutionService
from literature_agent.application.run_reconcile_service import RunReconcileService
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.run import RunType
from literature_agent.infrastructure.config import Settings
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
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.parse_revision_repository import (
    SqlalchemyParseRevisionRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from literature_agent.infrastructure.queue.arq_run_queue import ArqRunQueue
from literature_agent.infrastructure.queue.valkey_event_notifier import (
    ValkeyEventNotifier,
)
from literature_agent.infrastructure.storage.local_storage import LocalFileStorage

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


async def execute_run(ctx: dict[str, Any], run_id: str) -> str:
    """ARQ Job：执行一个 Run，只接收 run_id。

    Job 可安全重复执行：RunExecutionService 只在 QUEUED 状态认领 Run。
    """
    service: RunExecutionService = ctx["run_execution_service"]
    outcome = await service.execute(
        run_id,
        correlation_id=f"arq-job:{ctx.get('job_id', run_id)}",
    )
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
        except Exception:
            logger.exception("Outbox 派发循环出错")
        await asyncio.sleep(settings.outbox_poll_interval_seconds)


async def _reconcile_loop(ctx: dict[str, Any]) -> None:
    """周期性收回 lease 过期的执行中 Run（Worker 崩溃恢复）。"""
    service: RunReconcileService = ctx["run_reconcile_service"]
    settings: Settings = ctx["settings"]
    while True:
        try:
            recovered = await service.reconcile_expired()
            if recovered:
                logger.info("对账收回 %d 个过期 Run", recovered)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Run 对账循环出错")
        await asyncio.sleep(settings.worker_reconcile_interval_seconds)


async def _startup(ctx: dict[str, Any], settings: Settings) -> None:
    """Worker 启动：建立数据库、队列依赖并启动派发与对账循环。"""
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
    )
    dispatcher = RunDispatcher(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        executors={
            RunType.INGESTION: ingestion_executor.execute,
            RunType.INDEXING: indexing_executor.execute,
            RunType.RAG_ANSWER: rag_answer_executor.execute,
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
    )
    ctx["dispatch_task"] = asyncio.create_task(_dispatch_loop(ctx))
    ctx["reconcile_task"] = asyncio.create_task(_reconcile_loop(ctx))


async def _shutdown(ctx: dict[str, Any]) -> None:
    """Worker 关闭：取消后台循环并释放连接资源。"""
    for key in ("dispatch_task", "reconcile_task"):
        task: asyncio.Task | None = ctx.get(key)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
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

        functions = [execute_run]
        on_startup = startup
        on_shutdown = shutdown
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        max_tries = 1
        # ARQ Job 超时必须大于 Parser 超时，给状态提交留出余量
        job_timeout = int(settings.parser_timeout_seconds) + 60

    return WorkerSettings


def main() -> None:
    """Worker 进程入口。"""
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    run_worker(make_worker_settings(settings))


if __name__ == "__main__":
    main()
