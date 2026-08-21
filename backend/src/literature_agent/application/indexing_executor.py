"""Indexing 执行器：把一次索引 Run 推进到终态（chunking + embedding）。

流程：复用检查 → 读取 Element（短事务）→ ChunkBuilder 构建（事务外）
→ 提交 Chunk/映射（ChunkSet 保持 running，发 chunking_completed）
→ 分批 Embedding（事务外调用模型，每批一个短事务写回向量）
→ 最终事务提交 ChunkSet 就绪与 Run 终态。结构与
``IngestionExecutor`` 同构：每个短事务只做一件事，模型调用与 Chunk
构建发生在数据库事务外，取消检查分布在批次入口。

重跑语义（Effectively Once）：failed/running 遗留 ChunkSet 重置复用
同一行；chunks 已存在（上次已提交）则跳过 chunking，只补 embedding
为 null 的批次；``(chunk_set_id, sequence)`` 唯一约束兜底重复提交。
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.failure_policy import apply_run_failure
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.element_repository import ElementRepository
from literature_agent.application.ports.event_notifier import (
    EventNotifier,
    NoopEventNotifier,
)
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.parse_revision_repository import (
    ParseRevisionRepository,
)
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.chunk import (
    Chunk,
    ChunkElementLink,
    ChunkSet,
    ChunkSetStatus,
    create_chunk_set,
)
from literature_agent.domain.chunk_builder import ChunkDraft, build_chunks
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    IndexingInputError,
    RunConcurrentModificationError,
)
from literature_agent.domain.parse_revision import (
    DocumentParseRevision,
    ParseRevisionStatus,
)
from literature_agent.domain.run import Run, RunStatus, RunType

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_MAX_LENGTH = 500
# Element 分页读取批次大小
_ELEMENT_PAGE_SIZE = 500


class IndexingExecutor[TSession: Session]:
    """索引执行器，由 RunExecutionService 在认领 Run 后调用。

    不变量:
        - Chunk 构建（ChunkBuilder）与模型调用发生在数据库事务外；
        - Chunk/映射在同一事务提交后 ChunkSet 仍为 running，直到全部
          向量写回后才在最终事务与 Run 终态一起标记 ready；
        - Embedding 分批执行，每批一个短事务写回，批次间检查取消；
          取消后已写入的向量保留，重跑只补 null 批次；
        - 相同 (parse_revision_id, profile_hash) 已有 ready ChunkSet
          时复用，不重复切分也不调用模型；
        - 每次模型调用经 ModelGateway 记录（含 run_id）。
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        parse_revision_repo_factory: Callable[[TSession], ParseRevisionRepository],
        element_repo_factory: Callable[[TSession], ElementRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        chunk_repo_factory: Callable[[TSession], ChunkRepository],
        attempt_repo_factory: Callable[[TSession], AttemptRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        profile: ChunkProfile,
        model_gateway: ModelGateway[TSession],
        embedding_batch_size: int = 32,
        max_run_attempts: int = 3,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        """初始化 IndexingExecutor。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            parse_revision_repo_factory: 根据 session 创建 ParseRevisionRepository 的工厂。
            element_repo_factory: 根据 session 创建 ElementRepository 的工厂。
            chunk_set_repo_factory: 根据 session 创建 ChunkSetRepository 的工厂。
            chunk_repo_factory: 根据 session 创建 ChunkRepository 的工厂。
            attempt_repo_factory: 根据 session 创建 AttemptRepository 的工厂。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            profile: Chunk 切分配置画像（含当前活动 Embedding 参数）。
            model_gateway: 模型调用入口（统一计时与调用记录）。
            embedding_batch_size: 单次 Embedding 调用的文本数（批次大小）。
            max_run_attempts: 最大执行尝试次数（含首次），临时错误超出后 FAILED。
            event_notifier: 事件通知器，默认 Noop。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._parse_revision_repo_factory = parse_revision_repo_factory
        self._element_repo_factory = element_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._chunk_repo_factory = chunk_repo_factory
        self._attempt_repo_factory = attempt_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._profile = profile
        self._model_gateway = model_gateway
        self._embedding_batch_size = embedding_batch_size
        self._max_run_attempts = max_run_attempts
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def execute(self, run: Run, correlation_id: str) -> None:
        """执行一次索引 Run，自行推进终态。

        参数:
            run: 已认领的 RUNNING 状态 Run。
            correlation_id: 关联标识符。
        """
        # 防御：dispatcher 已按 run_type 分发，这里兜底双保险
        if run.run_type != RunType.INDEXING.value:
            raise ValueError(f"IndexingExecutor 收到非 indexing Run: {run.run_type}")
        parse_revision_id = run.input_payload.get("parse_revision_id", "")
        if not parse_revision_id:
            raise IndexingInputError("indexing Run 缺少 parse_revision_id 输入")

        # 事务 A：准备 ChunkSet（复用/创建/重置）并记录 indexing_started
        prepared = await self._prepare(run, parse_revision_id, correlation_id)
        if prepared is None:
            # 复用路径已在事务内完成终态提交，或 Run 已被取消
            return
        chunk_set, revision = prepared

        # chunking 阶段：重跑时 chunks 已提交则跳过，只补向量
        if await self._count_chunks(chunk_set.chunk_set_id) == 0:
            # 短事务：读取 Element 与来源定位
            elements, locations = await self._load_elements(revision.revision_id)

            # 事务外：构建 Chunk 草稿（纯函数，确定性）
            try:
                drafts = build_chunks(elements, locations, self._profile)
            except Exception as exc:
                logger.warning("Chunk 构建失败: run_id=%s", run.run_id, exc_info=True)
                await self._mark_failed(run, chunk_set, exc, correlation_id)
                return

            # 事务 C：提交 Chunk/映射（ChunkSet 保持 running）
            if not await self._commit_chunks(run, chunk_set, drafts, correlation_id):
                return  # 提交前命中取消

        # Embedding 阶段：分批补 embedding 为 null 的 Chunk
        prompt_tokens_total = await self._embed_pending(run, chunk_set, correlation_id)
        if prompt_tokens_total is None:
            return  # 已取消或失败（内部已推进终态）

        # 最终事务：ChunkSet ready + Run SUCCEEDED + 收尾事件
        await self._commit_ready(run, chunk_set, prompt_tokens_total, correlation_id)

    async def _prepare(
        self,
        run: Run,
        parse_revision_id: str,
        correlation_id: str,
    ) -> tuple[ChunkSet, DocumentParseRevision] | None:
        """事务 A：复用检查、准备 ChunkSet 行、写 indexing_started 事件。"""
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None:
                raise RunConcurrentModificationError(run.run_id)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return None

            revision = await self._parse_revision_repo_factory(session).get_by_id(
                parse_revision_id
            )
            if revision is None:
                raise IndexingInputError(f"ParseRevision {parse_revision_id} 不存在")
            if revision.status != ParseRevisionStatus.SUCCEEDED:
                raise IndexingInputError(
                    f"ParseRevision {parse_revision_id} 尚未解析成功"
                    f"（当前状态 {revision.status.value}）"
                )

            chunk_set_repo = self._chunk_set_repo_factory(session)
            chunk_set = await chunk_set_repo.get_by_revision_and_profile(
                parse_revision_id, self._profile.profile_hash
            )
            if chunk_set is not None and chunk_set.status == ChunkSetStatus.READY:
                # 复用已有切分结果：同一事务内推进终态
                chunk_count = await self._chunk_repo_factory(
                    session
                ).count_by_chunk_set(chunk_set.chunk_set_id)
                await self._finish_run(
                    session,
                    run_row,
                    RunStatus.SUCCEEDED,
                    "indexing_completed",
                    {
                        "chunk_set_id": chunk_set.chunk_set_id,
                        "chunk_count": chunk_count,
                        "reused": True,
                    },
                    correlation_id,
                )
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return None

            if chunk_set is None:
                chunk_set = create_chunk_set(
                    parse_revision_id=parse_revision_id,
                    profile_hash=self._profile.profile_hash,
                    config=self._profile.config,
                )
                await chunk_set_repo.add(chunk_set)
                await session.flush()
            else:
                # failed/running 遗留行：唯一约束不允许第二行，重置复用同一行
                chunk_set = chunk_set.reset_running()
                await chunk_set_repo.save(chunk_set)

            await self._emit_progress(
                session,
                run_row,
                "indexing_started",
                {
                    "chunk_set_id": chunk_set.chunk_set_id,
                    "profile_hash": self._profile.profile_hash,
                },
                correlation_id,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return chunk_set, revision

    async def _load_elements(
        self,
        revision_id: str,
    ) -> tuple[list[DocumentElement], list[ElementSourceLocation]]:
        """短事务：分页读取 Revision 的全部 Element 与来源定位。"""
        async with self._session_factory() as session:
            element_repo = self._element_repo_factory(session)
            elements: list[DocumentElement] = []
            offset = 0
            while True:
                batch = await element_repo.list_by_revision(
                    revision_id, limit=_ELEMENT_PAGE_SIZE, offset=offset
                )
                elements.extend(batch)
                if len(batch) < _ELEMENT_PAGE_SIZE:
                    break
                offset += _ELEMENT_PAGE_SIZE
            locations = await element_repo.list_locations(
                [e.element_id for e in elements]
            )
        return elements, locations

    async def _count_chunks(self, chunk_set_id: str) -> int:
        """短事务：统计 ChunkSet 已有 Chunk 数（判断重跑是否跳过 chunking）。"""
        async with self._session_factory() as session:
            return await self._chunk_repo_factory(session).count_by_chunk_set(chunk_set_id)

    async def _commit_chunks(
        self,
        run: Run,
        chunk_set: ChunkSet,
        drafts: list[ChunkDraft],
        correlation_id: str,
    ) -> bool:
        """事务 C：原子提交 Chunk/映射并记录 chunking_completed。

        ChunkSet 保持 running，待全部向量写回后才在最终事务标记 ready。
        返回 False 表示提交前命中取消（已推进 CANCELLED）。
        """
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None:
                raise RunConcurrentModificationError(run.run_id)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return False

            # 持久化 ID 在提交时分配，ChunkBuilder 保持确定性纯函数
            chunks = [
                Chunk(
                    chunk_id=str(uuid4()),
                    chunk_set_id=chunk_set.chunk_set_id,
                    sequence=draft.sequence,
                    text=draft.text,
                    token_count=draft.token_count,
                    section_path=draft.section_path,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    content_hash=draft.content_hash,
                )
                for draft in drafts
            ]
            links = [
                ChunkElementLink(
                    chunk_id=chunk.chunk_id,
                    element_id=element_id,
                    sequence=link_sequence,
                )
                for chunk, draft in zip(chunks, drafts, strict=True)
                for link_sequence, element_id in enumerate(draft.element_ids, start=1)
            ]
            chunk_repo = self._chunk_repo_factory(session)
            await chunk_repo.add_many(chunks)
            # Repository 只向 Session 注册独立 ORM 行，UOW 不会自动推导
            # 跨 Repository 的插入顺序：先落 Chunk 再落引用它的 links
            await session.flush()
            await chunk_repo.add_links(links)

            await self._emit_progress(
                session,
                run_row,
                "chunking_completed",
                {
                    "chunk_set_id": chunk_set.chunk_set_id,
                    "chunk_count": len(chunks),
                    "profile_hash": self._profile.profile_hash,
                },
                correlation_id,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return True

    async def _embed_pending(
        self,
        run: Run,
        chunk_set: ChunkSet,
        correlation_id: str,
    ) -> int | None:
        """分批为 embedding 为 null 的 Chunk 生成并写回向量。

        每批：短事务（持锁检查取消 + 读取待处理批次）→ 事务外经
        ModelGateway 调用模型 → 短事务写回向量。取消时保留已写入向量，
        重跑只补 null 批次；空 ChunkSet 不调用模型。

        返回本次运行的 prompt token 总量；取消或失败时返回 None
        （终态已在内部推进）。
        """
        prompt_tokens_total = 0
        while True:
            async with self._session_factory() as session:
                run_repo = self._run_repo_factory(session)
                run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
                if run_row is None:
                    raise RunConcurrentModificationError(run.run_id)
                if await self._finalize_if_cancelled(session, run_row, correlation_id):
                    await session.commit()
                    await notify_run_event(self._event_notifier, run.run_id)
                    return None
                pending = await self._chunk_repo_factory(
                    session
                ).list_pending_embedding(
                    chunk_set.chunk_set_id, self._embedding_batch_size
                )
            if not pending:
                return prompt_tokens_total

            # 模型调用不发生在数据库事务内；每次调用记录含 run_id
            try:
                result = await self._model_gateway.embed(
                    [chunk.text for chunk in pending], run_id=run.run_id
                )
            except Exception as exc:
                logger.warning("Embedding 调用失败: run_id=%s", run.run_id, exc_info=True)
                await self._mark_failed(run, chunk_set, exc, correlation_id)
                return None

            async with self._session_factory() as session:
                await self._chunk_repo_factory(session).save_embeddings(
                    {
                        chunk.chunk_id: vector
                        for chunk, vector in zip(pending, result.vectors, strict=True)
                    }
                )
                await session.commit()
            prompt_tokens_total += result.usage.prompt_tokens or 0

    async def _commit_ready(
        self,
        run: Run,
        chunk_set: ChunkSet,
        prompt_tokens_total: int,
        correlation_id: str,
    ) -> None:
        """最终事务：ChunkSet 就绪、Run 终态与收尾事件原子提交。"""
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None:
                raise RunConcurrentModificationError(run.run_id)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return

            chunk_repo = self._chunk_repo_factory(session)
            chunk_count = await chunk_repo.count_by_chunk_set(chunk_set.chunk_set_id)
            embedded_count = await chunk_repo.count_embedded(chunk_set.chunk_set_id)
            await self._chunk_set_repo_factory(session).save(chunk_set.mark_ready(now))

            await self._emit_progress(
                session,
                run_row,
                "embedding_completed",
                {
                    "chunk_set_id": chunk_set.chunk_set_id,
                    "embedded_count": embedded_count,
                    "prompt_tokens": prompt_tokens_total,
                },
                correlation_id,
            )
            # Fake/真实实现均以数据库为准，重新读取最新 sequence
            fresh = await run_repo.get_by_id(run.run_id)
            run_row = fresh if fresh is not None else run_row
            await self._finish_run(
                session,
                run_row,
                RunStatus.SUCCEEDED,
                "indexing_completed",
                {
                    "chunk_set_id": chunk_set.chunk_set_id,
                    "chunk_count": chunk_count,
                    "reused": False,
                },
                correlation_id,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)

    async def _mark_failed(
        self,
        run: Run,
        chunk_set: ChunkSet,
        exc: Exception,
        correlation_id: str,
    ) -> None:
        """构建或 Embedding 失败：ChunkSet 标记 FAILED，Run 按错误分类 FAILED 或 RETRY_WAIT。"""
        now = datetime.now(UTC)
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:_ERROR_MESSAGE_MAX_LENGTH],
        }
        async with self._session_factory() as session:
            await self._chunk_set_repo_factory(session).save(
                chunk_set.mark_failed(error, now)
            )
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
    ) -> None:
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
