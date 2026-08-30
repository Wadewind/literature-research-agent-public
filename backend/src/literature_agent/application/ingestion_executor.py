"""Ingestion 执行器：把一次文献导入 Run 推进到终态。

流程：复用检查 → 解析（事务外）→ 规范化 → 原子提交结果与 Run 终态。
真正的 Parser 由 ``DocumentParser`` Port 注入（切片 6 为 Fake Parser，
切片 7 起为 Docling + pypdf 降级组合）。Parser 超时在本层通过
``asyncio.wait_for`` 统一施加，适配器不自行实现超时。
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.failure_policy import apply_run_failure
from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.document_parser import DocumentParser
from literature_agent.application.ports.element_repository import ElementRepository
from literature_agent.application.ports.event_notifier import (
    EventNotifier,
    NoopEventNotifier,
)
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.application.ports.parse_revision_repository import (
    ParseRevisionRepository,
)
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.document_element import (
    ElementType,
    ParsedDocument,
    detect_document_warnings,
    normalize_parsed_document,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.paper import PaperTitleSource
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import (
    DocumentParseRevision,
    ParseRevisionStatus,
    create_parse_revision,
)
from literature_agent.domain.queue_outbox import create_outbox_entry
from literature_agent.domain.run import Run, RunStatus, RunType, create_run

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_MAX_LENGTH = 500


class IngestionExecutor[TSession: Session]:
    """文献导入执行器，由 RunExecutionService 在认领 Run 后调用。

    不变量:
        - Parser 调用发生在数据库事务外；
        - 解析产物、当前 Revision 指针、Run 终态和 ``result_committed``
          Event 在同一事务原子提交，不暴露半成品；
        - 相同 (version_id, profile_hash) 已有成功 Revision 时复用；
        - 提交前检查取消，取消后不提交新结果。
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        parse_revision_repo_factory: Callable[[TSession], ParseRevisionRepository],
        element_repo_factory: Callable[[TSession], ElementRepository],
        attempt_repo_factory: Callable[[TSession], AttemptRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        parser: DocumentParser,
        profile: ParseProfile,
        parser_timeout_seconds: float = 300.0,
        max_run_attempts: int = 3,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        """初始化 IngestionExecutor。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            paper_repo_factory: 根据 session 创建 PaperRepository 的工厂。
            paper_version_repo_factory: 根据 session 创建 PaperVersionRepository 的工厂。
            parse_revision_repo_factory: 根据 session 创建 ParseRevisionRepository 的工厂。
            element_repo_factory: 根据 session 创建 ElementRepository 的工厂。
            attempt_repo_factory: 根据 session 创建 AttemptRepository 的工厂。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            parser: 文档解析器实现。
            profile: 解析配置画像。
            parser_timeout_seconds: 单次解析的超时秒数，超时按 FAILED 处理且不降级。
            max_run_attempts: 最大执行尝试次数（含首次），临时错误超出后 FAILED。
            event_notifier: 事件通知器，默认 Noop（切片 9，SSE 降延迟用）。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._parse_revision_repo_factory = parse_revision_repo_factory
        self._element_repo_factory = element_repo_factory
        self._attempt_repo_factory = attempt_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._parser = parser
        self._profile = profile
        self._parser_timeout_seconds = parser_timeout_seconds
        self._max_run_attempts = max_run_attempts
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def execute(self, run: Run, correlation_id: str) -> None:
        """执行一次导入 Run，自行推进终态。

        参数:
            run: 已认领的 RUNNING 状态 Run。
            correlation_id: 关联标识符。
        """
        # 防御：dispatcher 已按 run_type 分发，这里兜底双保险
        if run.run_type != RunType.INGESTION.value:
            raise ValueError(f"IngestionExecutor 收到非 ingestion Run: {run.run_type}")
        version_id = run.input_payload.get("version_id", "")

        # 事务 A：准备 Revision（复用/创建/重置）并记录 parse_started
        prepared = await self._prepare(run, version_id, correlation_id)
        if prepared is None:
            # 复用路径已在事务内完成终态提交，或 Run 已被取消
            return
        revision, storage_key = prepared

        # 事务外：调用 Parser（超时在本层统一施加，不触发降级）
        try:
            parsed = await asyncio.wait_for(
                self._parser.parse(storage_key, self._profile),
                timeout=self._parser_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("解析超时: run_id=%s", run.run_id)
            await self._mark_failed(
                run,
                revision,
                None,
                correlation_id,
                error={
                    "type": "parser_timeout",
                    "message": f"解析超过 {self._parser_timeout_seconds} 秒",
                },
            )
            return
        except Exception as exc:
            logger.warning("解析失败: run_id=%s", run.run_id, exc_info=True)
            await self._mark_failed(run, revision, exc, correlation_id)
            return

        # 事务 B：解析与规范化完成的进度事件
        normalized = normalize_parsed_document(revision.revision_id, parsed)
        if await self._emit_progress_batch(
            run,
            [
                ("parse_completed", {"element_count": len(normalized[0])}),
                ("normalize_completed", {}),
            ],
            correlation_id,
        ):
            return  # 执行期间被取消

        # 事务 C：原子提交解析产物与 Run 终态
        await self._commit_success(run, revision, parsed, normalized, correlation_id)

    async def _prepare(
        self,
        run: Run,
        version_id: str,
        correlation_id: str,
    ) -> tuple[DocumentParseRevision, str] | None:
        """事务 A：复用检查、准备 Revision 行、写 parse_started 事件。"""
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            version_repo = self._paper_version_repo_factory(session)
            revision_repo = self._parse_revision_repo_factory(session)

            version = await version_repo.get_by_id(version_id)
            if version is None:
                raise ValueError(f"PaperVersion {version_id} 不存在")

            revision = await revision_repo.get_by_version_and_profile(
                version_id, self._profile.profile_hash
            )
            if revision is not None and revision.status == ParseRevisionStatus.SUCCEEDED:
                # 复用已有成功结果：同一事务内设置指针并推进终态
                run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
                if run_row is None:
                    raise RunConcurrentModificationError(run.run_id)
                if await self._finalize_if_cancelled(session, run_row, correlation_id):
                    await session.commit()
                    await notify_run_event(self._event_notifier, run.run_id)
                    return None
                await self._commit_reuse(session, run_row, revision, correlation_id)
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return None

            if revision is None:
                revision = create_parse_revision(
                    version_id=version_id,
                    parser_name=self._profile.parser_name,
                    parser_version=self._profile.parser_version,
                    parser_profile_hash=self._profile.profile_hash,
                    config=self._profile.config,
                )
                await revision_repo.add(revision)
                await session.flush()
            elif revision.status == ParseRevisionStatus.FAILED:
                # 失败后重跑：复用同一行（唯一约束不允许第二行），重置为 RUNNING
                revision = DocumentParseRevision(
                    revision_id=revision.revision_id,
                    version_id=revision.version_id,
                    parser_name=revision.parser_name,
                    parser_version=revision.parser_version,
                    parser_profile_hash=revision.parser_profile_hash,
                    status=ParseRevisionStatus.RUNNING,
                    config=revision.config,
                    error=None,
                    created_at=revision.created_at,
                    completed_at=None,
                )
                await revision_repo.save(revision)
            # RUNNING 状态的旧行视为上次崩溃遗留，直接复用该行

            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None:
                raise RunConcurrentModificationError(run.run_id)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return None
            await self._emit_progress(
                session, run_row, "parse_started",
                {"revision_id": revision.revision_id}, correlation_id,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return revision, version.storage_key

    async def _commit_reuse(
        self,
        session: TSession,
        run_row: Run,
        revision: DocumentParseRevision,
        correlation_id: str,
    ) -> None:
        """复用路径：设置当前指针并把 Run 推进到 SUCCEEDED（须持锁事务内调用）。"""
        element_count = await self._element_repo_factory(session).count_by_revision(
            revision.revision_id
        )
        await self._paper_version_repo_factory(session).set_current_parse_revision(
            revision.version_id, revision.revision_id
        )
        await self._finish_run(
            session,
            run_row,
            RunStatus.SUCCEEDED,
            "result_committed",
            {
                "revision_id": revision.revision_id,
                "element_count": element_count,
                "reused": True,
            },
            correlation_id,
        )
        # 解析成功必然跟随索引：同事务创建 indexing Run + Event + Outbox
        await self._create_indexing_run(session, run_row, revision, correlation_id)

    async def _create_indexing_run(
        self,
        session: TSession,
        source_run: Run,
        revision: DocumentParseRevision,
        correlation_id: str,
    ) -> None:
        """在结果提交事务内创建后续 indexing Run（Run + Event + Outbox 原子）。

        indexing Run 归属与触发它的 ingestion Run 相同（project/owner）；
        ``input_payload`` 携带 ``parse_revision_id`` 与冗余的 ``version_id``
        （用于事件与排查）。
        """
        indexing_run = create_run(
            project_id=source_run.project_id,
            owner_id=source_run.owner_id,
            run_type=RunType.INDEXING,
            input_payload={
                "parse_revision_id": revision.revision_id,
                "version_id": revision.version_id,
            },
        )
        created_event = create_event(
            run_id=indexing_run.run_id,
            sequence=1,
            event_type="run_created",
            actor_type="system",
            correlation_id=correlation_id,
            payload={"status": indexing_run.status.value},
        )
        # run_created 事件占用 sequence 1，Run 推进到 2 再入库
        await self._run_repo_factory(session).add(
            replace(indexing_run, event_sequence=2)
        )
        await session.flush()
        await self._event_repo_factory(session).add(created_event)
        await self._outbox_repo_factory(session).add(
            create_outbox_entry(indexing_run.run_id)
        )

    async def _commit_success(
        self,
        run: Run,
        revision: DocumentParseRevision,
        parsed: ParsedDocument,
        normalized: tuple[list, list],
        correlation_id: str,
    ) -> None:
        """事务 C：原子提交解析产物、当前指针、Run 终态和 result_committed 事件。"""
        elements, locations = normalized
        now = datetime.now(UTC)
        # 文档级警告 = Parser 自报（降级能力缺失等）+ 领域规则（possibly_scanned）
        warnings = [*parsed.warnings, *detect_document_warnings(parsed)]
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None:
                raise RunConcurrentModificationError(run.run_id)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return

            await self._element_repo_factory(session).add_many(elements)
            await self._element_repo_factory(session).add_locations(locations)
            await self._parse_revision_repo_factory(session).save(
                revision.mark_succeeded(now, degraded=parsed.degraded, warnings=warnings)
            )
            await self._paper_version_repo_factory(session).set_current_parse_revision(
                revision.version_id, revision.revision_id
            )
            parsed_title = next(
                (
                    element.text
                    for element in parsed.elements
                    if element.element_type is ElementType.TITLE and element.text
                ),
                None,
            )
            if parsed_title is not None:
                version = await self._paper_version_repo_factory(session).get_by_id(
                    revision.version_id
                )
                paper = (
                    await self._paper_repo_factory(session).get_by_id(version.paper_id)
                    if version is not None
                    else None
                )
                if paper is not None:
                    titled_paper = paper.with_title(
                        parsed_title, PaperTitleSource.PARSED_DOCUMENT
                    )
                    if titled_paper is not paper:
                        await self._paper_repo_factory(session).update(titled_paper)
            await self._finish_run(
                session,
                run_row,
                RunStatus.SUCCEEDED,
                "result_committed",
                {"revision_id": revision.revision_id, "element_count": len(elements),
                 "reused": False},
                correlation_id,
            )
            # 解析成功必然跟随索引：同事务创建 indexing Run + Event + Outbox
            await self._create_indexing_run(session, run_row, revision, correlation_id)
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)

    async def _mark_failed(
        self,
        run: Run,
        revision: DocumentParseRevision,
        exc: Exception | None,
        correlation_id: str,
        *,
        error: dict | None = None,
    ) -> None:
        """解析失败：Revision 标记 FAILED，Run 按错误分类 FAILED 或 RETRY_WAIT。"""
        now = datetime.now(UTC)
        if error is None:
            assert exc is not None
            error = {
                "type": type(exc).__name__,
                "message": str(exc)[:_ERROR_MESSAGE_MAX_LENGTH],
            }
        async with self._session_factory() as session:
            revision_repo = self._parse_revision_repo_factory(session)
            await revision_repo.save(revision.mark_failed(error, now))
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None or run_row.status != RunStatus.RUNNING:
                await session.commit()
                return
            # 按错误分类：永久输入错误直接 FAILED；临时错误预算内 RETRY_WAIT
            await apply_run_failure(
                session,
                run_repo_factory=self._run_repo_factory,
                event_repo_factory=self._event_repo_factory,
                attempt_repo_factory=self._attempt_repo_factory,
                outbox_repo_factory=self._outbox_repo_factory,
                run=run_row,
                error=error,
                exc=exc,
                correlation_id=correlation_id,
                max_run_attempts=self._max_run_attempts,
                now=now,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)

    async def _finalize_if_cancelled(
        self,
        session: TSession,
        run_row: Run,
        correlation_id: str,
    ) -> bool:
        """若 Run 已被请求取消，则推进 CANCELLED 并返回 True。"""
        if run_row.status != RunStatus.CANCEL_REQUESTED:
            return False
        await self._finish_run(
            session, run_row, RunStatus.CANCELLED, "run_cancelled", {}, correlation_id
        )
        return True

    async def _finish_run(
        self,
        session: TSession,
        run_row: Run,
        target: RunStatus,
        event_type: str,
        payload: dict,
        correlation_id: str,
    ) -> None:
        """在持锁事务内推进 Run 状态并写入事件。"""
        run_repo = self._run_repo_factory(session)
        # 领域层校验转换合法性
        run_row.transition_to(target)
        updated = await run_repo.update_status(
            run_id=run_row.run_id,
            expected_status=run_row.status,
            new_status=target,
            new_event_sequence=run_row.event_sequence + 1,
        )
        if not updated:
            raise RunConcurrentModificationError(run_row.run_id)
        await self._event_repo_factory(session).add(
            create_event(
                run_id=run_row.run_id,
                sequence=run_row.event_sequence,
                event_type=event_type,
                actor_type="system",
                correlation_id=correlation_id,
                payload=payload,
            )
        )

    async def _emit_progress(
        self,
        session: TSession,
        run_row: Run,
        event_type: str,
        payload: dict,
        correlation_id: str,
    ) -> Run:
        """在事务内写入进度事件并推进 event_sequence（状态不变）。"""
        run_repo = self._run_repo_factory(session)
        await self._event_repo_factory(session).add(
            create_event(
                run_id=run_row.run_id,
                sequence=run_row.event_sequence,
                event_type=event_type,
                actor_type="system",
                correlation_id=correlation_id,
                payload=payload,
            )
        )
        updated = await run_repo.update_status(
            run_id=run_row.run_id,
            expected_status=run_row.status,
            new_status=run_row.status,
            new_event_sequence=run_row.event_sequence + 1,
        )
        if not updated:
            raise RunConcurrentModificationError(run_row.run_id)
        return run_row

    async def _emit_progress_batch(
        self,
        run: Run,
        events: list[tuple[str, dict]],
        correlation_id: str,
    ) -> bool:
        """在单个事务内批量写进度事件；返回 True 表示 Run 已被取消。"""
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None:
                raise RunConcurrentModificationError(run.run_id)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return True
            for event_type, payload in events:
                await self._emit_progress(session, run_row, event_type, payload, correlation_id)
                # Fake/真实实现均以数据库为准，重新读取最新 sequence
                fresh = await run_repo.get_by_id(run.run_id)
                run_row = fresh if fresh is not None else run_row
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return False
