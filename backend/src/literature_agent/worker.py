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
from contextlib import suppress
from typing import Any

from arq.connections import RedisSettings
from arq.worker import run_worker

from literature_agent.application.ingestion_executor import IngestionExecutor
from literature_agent.application.outbox_dispatch_service import OutboxDispatchService
from literature_agent.application.ports.document_parser import DocumentParser
from literature_agent.application.run_execution_service import RunExecutionService
from literature_agent.application.run_reconcile_service import RunReconcileService
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.parsing.fake_parser import (
    FakeDocumentParser,
)
from literature_agent.infrastructure.parsing.fallback_parser import FallbackDocumentParser
from literature_agent.infrastructure.parsing.pypdf_parser import PypdfDocumentParser
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
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
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["queue"] = queue
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
    ctx["run_execution_service"] = RunExecutionService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        executor=IngestionExecutor(
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
        ).execute,
        worker_id=worker_id,
        heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
        max_run_attempts=settings.max_run_attempts,
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
