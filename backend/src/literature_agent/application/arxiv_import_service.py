"""arXiv 检索、受限下载与项目导入应用服务。"""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.arxiv_gateway import ArxivGateway
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import PaperVersionRepository
from literature_agent.application.ports.project_paper_repository import ProjectPaperRepository
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.arxiv import ArxivError, ArxivPaper, ArxivSearchQuery, DownloadedPdf
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    ProjectArchivedError,
    ProjectNotFoundError,
    RunNotFoundError,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.queue_outbox import create_outbox_entry
from literature_agent.domain.review import (
    ReviewDependencyStatus,
    ReviewDependencyType,
    ReviewSource,
    ReviewSourceStatus,
    ReviewStepKey,
    ReviewStepStatus,
    create_review_dependency,
    create_review_source,
    create_run_step,
)
from literature_agent.domain.run import Run, RunStatus, RunType, create_run


@dataclass(frozen=True, slots=True)
class ArxivImportSummary:
    """一次来源导入批次的小型结果摘要。"""

    discovered: int
    imported: int
    ready: int
    failed: int
    downloaded_bytes: int


class ArxivProjectImportService[TSession: Session]:
    """在外部 I/O 与短数据库事务之间建立明确边界。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        arxiv_gateway: ArxivGateway,
        storage: Storage,
        project_repo_factory: Callable[[TSession], ProjectRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        project_paper_repo_factory: Callable[[TSession], ProjectPaperRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        total_download_budget_bytes: int,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        if total_download_budget_bytes < 1:
            raise ValueError("arxiv_total_download_budget_invalid")
        self._session_factory = session_factory
        self._arxiv = arxiv_gateway
        self._storage = storage
        self._project_repo_factory = project_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._project_paper_repo_factory = project_paper_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._review_repo_factory = review_repo_factory
        self._total_download_budget_bytes = total_download_budget_bytes
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def search_sources(
        self,
        *,
        actor: ActorContext,
        project_id: str,
        review_run_id: str,
        query: ArxivSearchQuery,
        correlation_id: str,
    ) -> list[ReviewSource]:
        """先授权并复用完成事实，再事务外检索、事务内固化快照。"""
        search_key = _search_idempotency_key(query)
        # 不持锁出网。这个短读同时确保越权请求不会触发外部 I/O。
        async with self._session_factory() as session:
            await self._lock_review_scope(session, actor, project_id, review_run_id)
            review_repo = self._review_repo_factory(session)
            completed = await self._completed_search(
                review_repo, review_run_id, project_id, actor.owner_id, search_key
            )
            if completed:
                return await review_repo.list_sources_scoped(
                    review_run_id, project_id, actor.owner_id
                )
            existing_steps = await review_repo.list_steps_scoped(
                review_run_id, project_id, actor.owner_id
            )
            if any(
                step.step_key is ReviewStepKey.SEARCH_ARXIV
                for step in existing_steps
            ):
                raise ValueError("arxiv_search_replay_conflict")
        papers = await self._arxiv.search(query)
        async with self._session_factory() as session:
            run, review_repo = await self._lock_review_scope(
                session, actor, project_id, review_run_id
            )
            # 并发请求在父 Run 行锁后重新检查，只有一个写入 Event/Step/Source。
            if await self._completed_search(
                review_repo, review_run_id, project_id, actor.owner_id, search_key
            ):
                return await review_repo.list_sources_scoped(
                    review_run_id, project_id, actor.owner_id
                )
            steps = await review_repo.list_steps_scoped(
                review_run_id, project_id, actor.owner_id
            )
            if any(step.step_key is ReviewStepKey.SEARCH_ARXIV for step in steps):
                raise ValueError("arxiv_search_replay_conflict")
            sources = [
                create_review_source(
                    review_run_id=review_run_id,
                    arxiv_id=paper.arxiv_id,
                    arxiv_version=paper.arxiv_version,
                    rank=rank,
                    metadata_snapshot=_metadata_snapshot(paper),
                )
                for rank, paper in enumerate(papers, start=1)
            ]
            for source in sources:
                await review_repo.add_source(source)
            now = datetime.now(UTC)
            await review_repo.add_step(
                replace(
                    create_run_step(
                        run_id=review_run_id,
                        step_key=ReviewStepKey.SEARCH_ARXIV,
                        sequence=max((step.sequence for step in steps), default=0) + 1,
                        idempotency_key=search_key,
                        input_refs={"query_hash": search_key.removeprefix("arxiv-search:")},
                    ),
                    status=ReviewStepStatus.SUCCEEDED,
                    output_refs={"source_count": len(sources)},
                    started_at=now,
                    completed_at=now,
                )
            )
            await self._append_parent_event(
                session,
                run,
                "arxiv_search_completed",
                correlation_id,
                {"source_count": len(sources)},
            )
            await session.commit()
        await notify_run_event(self._event_notifier, review_run_id)
        return sources

    async def import_sources(
        self,
        *,
        actor: ActorContext,
        project_id: str,
        review_run_id: str,
        correlation_id: str,
    ) -> ArxivImportSummary:
        """逐篇下载和缓存；单篇失败稳定落库且不阻止后续来源。"""
        async with self._session_factory() as session:
            await self._lock_review_scope(session, actor, project_id, review_run_id)
            sources = await self._review_repo_factory(session).list_sources_scoped(
                review_run_id, project_id, actor.owner_id
            )
            version_repo = self._paper_version_repo_factory(session)
            consumed_version_ids: set[str] = set()
            downloaded_bytes = 0
            for source in sources:
                if (
                    source.paper_version_id is None
                    or source.paper_version_id in consumed_version_ids
                ):
                    continue
                version = await version_repo.get_by_id(source.paper_version_id)
                if version is None or version.owner_id != actor.owner_id:
                    raise ValueError("review_source_version_scope_mismatch")
                consumed_version_ids.add(version.version_id)
                downloaded_bytes += version.size_bytes

        imported = ready = failed = 0
        for source in sources:
            if source.status in {ReviewSourceStatus.IMPORTING, ReviewSourceStatus.READY}:
                imported += 1
                ready += int(source.status is ReviewSourceStatus.READY)
                continue
            if source.status is ReviewSourceStatus.FAILED:
                failed += 1
                continue
            try:
                downloaded = await self._arxiv.download_pdf(
                    str(source.metadata_snapshot["pdf_url"]),
                    remaining_budget_bytes=self._total_download_budget_bytes
                    - downloaded_bytes,
                )
                downloaded_bytes += len(downloaded.content)
                # owner + sha256 是唯一进入 key 的可变部分；不使用 arXiv/title。
                storage_key = _cache_storage_key(actor.owner_id, downloaded.content_hash)
                # 写入发生在数据库事务之外。并发/事务失败留下可安全复用和对账的缓存，
                # 不做可能删除其他执行者对象的补偿删除。
                await self._storage.write(storage_key, downloaded.content)
                was_ready, created_run_id = await self._register_download(
                    actor=actor,
                    project_id=project_id,
                    review_run_id=review_run_id,
                    source_id=source.source_id,
                    downloaded=downloaded,
                    storage_key=storage_key,
                    correlation_id=correlation_id,
                )
                imported += 1
                ready += int(was_ready)
                await notify_run_event(self._event_notifier, review_run_id)
                if created_run_id is not None:
                    await notify_run_event(self._event_notifier, created_run_id)
            except ArxivError as exc:
                if exc.temporary:
                    # Adapter 已完成受限重试；仍为临时错误时交给 Run 重试，
                    # 不把可恢复故障固化成单篇永久失败。
                    raise
                await self._mark_source_failed(
                    actor,
                    project_id,
                    review_run_id,
                    source.source_id,
                    exc.code,
                    correlation_id,
                )
                failed += 1
        return ArxivImportSummary(
            discovered=len(sources),
            imported=imported,
            ready=ready,
            failed=failed,
            downloaded_bytes=downloaded_bytes,
        )

    async def _register_download(
        self,
        *,
        actor: ActorContext,
        project_id: str,
        review_run_id: str,
        source_id: str,
        downloaded: DownloadedPdf,
        storage_key: str,
        correlation_id: str,
    ) -> tuple[bool, str | None]:
        async with self._session_factory() as session:
            parent_run, review_repo = await self._lock_review_scope(
                session, actor, project_id, review_run_id
            )
            source = await review_repo.get_source_scoped_for_update(
                source_id, review_run_id, project_id, actor.owner_id
            )
            if source is None:
                raise RunNotFoundError(source_id)
            if source.status is not ReviewSourceStatus.DISCOVERED:
                return source.status is ReviewSourceStatus.READY, None

            version_repo = self._paper_version_repo_factory(session)
            await version_repo.acquire_owner_hash_lock(actor.owner_id, downloaded.content_hash)
            version = await version_repo.get_by_owner_and_hash(
                actor.owner_id, downloaded.content_hash
            )
            created_run_id: str | None = None
            if version is None:
                paper = create_paper(actor.owner_id)
                ingestion_run = create_run(
                    project_id=project_id,
                    owner_id=actor.owner_id,
                    run_type=RunType.INGESTION,
                    input_payload={},
                )
                version = create_paper_version(
                    paper_id=paper.paper_id,
                    owner_id=actor.owner_id,
                    file_hash=downloaded.content_hash,
                    storage_key=storage_key,
                    size_bytes=len(downloaded.content),
                    content_type=downloaded.media_type,
                    display_filename=(
                        f"{source.arxiv_id.replace('/', '_')}"
                        f"{source.arxiv_version}.pdf"
                    ),
                    ingestion_run_id=ingestion_run.run_id,
                )
                ingestion_run = replace(
                    ingestion_run,
                    input_payload={
                        "paper_id": paper.paper_id,
                        "version_id": version.version_id,
                        "filename": version.display_filename,
                        "content_type": version.content_type,
                        "file_hash": version.file_hash,
                    },
                    event_sequence=2,
                )
                await self._paper_repo_factory(session).add(paper)
                await self._run_repo_factory(session).add(ingestion_run)
                await session.flush()
                await version_repo.add(version)
                await session.flush()
                await self._project_paper_repo_factory(session).add(
                    create_project_paper(project_id, paper.paper_id, version.version_id)
                )
                await self._event_repo_factory(session).add(
                    create_event(
                        run_id=ingestion_run.run_id,
                        sequence=1,
                        event_type="run_created",
                        actor_type="system",
                        correlation_id=correlation_id,
                        payload={"status": RunStatus.QUEUED.value},
                    )
                )
                await self._outbox_repo_factory(session).add(
                    create_outbox_entry(ingestion_run.run_id)
                )
                created_run_id = ingestion_run.run_id
            else:
                paper = await self._paper_repo_factory(session).get_by_id(version.paper_id)
                if paper is None or paper.owner_id != actor.owner_id:
                    raise ValueError("review_source_paper_owner_mismatch")
                if paper.is_archived:
                    # 与 ProjectLibrary/Phase1 上传保持一致：不静默恢复用户已归档论文。
                    # 直接失败也优于绑定一个后续 RAG 范围不可见的来源。
                    raise ArxivError("review_source_paper_archived", temporary=False)
                relation_repo = self._project_paper_repo_factory(session)
                if await relation_repo.get(project_id, paper.paper_id) is None:
                    await relation_repo.add(
                        create_project_paper(project_id, paper.paper_id, version.version_id)
                    )

            # 这里的查询通过 Revision→Version 连接验证 ChunkSet 确实属于该 Version。
            ready_chunk_set = await self._chunk_set_repo_factory(session).get_ready_by_version(
                version.version_id
            )
            current_source = (
                source.mark_ready(version.paper_id, version.version_id)
                if ready_chunk_set is not None
                else source.mark_importing(version.paper_id, version.version_id)
            )
            await review_repo.save_source(current_source)
            dependencies = await review_repo.list_dependencies_scoped(
                review_run_id, project_id, actor.owner_id
            )
            targets = {
                (dependency.dependency_type, dependency.target_run_id,
                 dependency.target_paper_version_id, dependency.target_chunk_set_id)
                for dependency in dependencies
            }
            version_key = (ReviewDependencyType.PAPER_VERSION, None, version.version_id, None)
            if version_key not in targets:
                dependency = create_review_dependency(
                    parent_run_id=review_run_id,
                    dependency_type=ReviewDependencyType.PAPER_VERSION,
                    target_paper_version_id=version.version_id,
                )
                if ready_chunk_set is not None:
                    dependency = replace(
                        dependency,
                        status=ReviewDependencyStatus.SATISFIED,
                        satisfied_at=datetime.now(UTC),
                    )
                await review_repo.add_dependency(dependency)
            if ready_chunk_set is not None:
                chunk_key = (
                    ReviewDependencyType.CHUNK_SET,
                    None,
                    None,
                    ready_chunk_set.chunk_set_id,
                )
                if chunk_key not in targets:
                    dependency = create_review_dependency(
                        parent_run_id=review_run_id,
                        dependency_type=ReviewDependencyType.CHUNK_SET,
                        target_chunk_set_id=ready_chunk_set.chunk_set_id,
                    )
                    await review_repo.add_dependency(
                        replace(
                            dependency,
                            status=ReviewDependencyStatus.SATISFIED,
                            satisfied_at=datetime.now(UTC),
                        )
                    )
            elif version.ingestion_run_id is not None:
                ingestion_run = await self._run_repo_factory(session).get_by_id(
                    version.ingestion_run_id
                )
                # 同 owner 跨 Project Version 可以复用，但父 Review 不能等待另一个
                # Project 的 Run；这种情况只保留 PaperVersion 依赖供切片4对账。
                run_key = (ReviewDependencyType.RUN, version.ingestion_run_id, None, None)
                if (
                    run_key not in targets
                    and ingestion_run is not None
                    and ingestion_run.owner_id == actor.owner_id
                    and ingestion_run.project_id == project_id
                    and ingestion_run.run_type == RunType.INGESTION.value
                ):
                    await review_repo.add_dependency(
                        create_review_dependency(
                            parent_run_id=review_run_id,
                            dependency_type=ReviewDependencyType.RUN,
                            target_run_id=version.ingestion_run_id,
                        )
                    )
            await self._append_parent_event(
                session,
                parent_run,
                "review_source_ready" if ready_chunk_set else "review_source_import_started",
                correlation_id,
                {
                    "source_id": source.source_id,
                    "paper_id": version.paper_id,
                    "paper_version_id": version.version_id,
                },
            )
            await session.commit()
            return ready_chunk_set is not None, created_run_id

    @staticmethod
    async def _completed_search(
        review_repo: ReviewRepository,
        review_run_id: str,
        project_id: str,
        owner_id: str,
        search_key: str,
    ) -> bool:
        steps = await review_repo.list_steps_scoped(
            review_run_id, project_id, owner_id
        )
        return any(
            step.step_key is ReviewStepKey.SEARCH_ARXIV
            and step.status is ReviewStepStatus.SUCCEEDED
            and step.idempotency_key == search_key
            for step in steps
        )

    async def _mark_source_failed(
        self,
        actor: ActorContext,
        project_id: str,
        review_run_id: str,
        source_id: str,
        failure_code: str,
        correlation_id: str,
    ) -> None:
        async with self._session_factory() as session:
            parent_run, review_repo = await self._lock_review_scope(
                session, actor, project_id, review_run_id
            )
            source = await review_repo.get_source_scoped_for_update(
                source_id, review_run_id, project_id, actor.owner_id
            )
            if source is None:
                raise RunNotFoundError(source_id)
            if source.status is not ReviewSourceStatus.DISCOVERED:
                return
            await review_repo.save_source(source.mark_failed(failure_code))
            await self._append_parent_event(
                session,
                parent_run,
                "review_source_failed",
                correlation_id,
                {"source_id": source.source_id, "failure_code": failure_code},
            )
            await session.commit()
        await notify_run_event(self._event_notifier, review_run_id)

    async def _lock_review_scope(
        self,
        session: TSession,
        actor: ActorContext,
        project_id: str,
        review_run_id: str,
    ) -> tuple[Run, ReviewRepository]:
        project = await self._project_repo_factory(session).get_by_id(project_id)
        if project is None or project.owner_id != actor.owner_id:
            raise ProjectNotFoundError(project_id)
        if project.is_archived:
            raise ProjectArchivedError(project_id)
        run = await self._run_repo_factory(session).get_by_id_for_update(
            review_run_id, actor.owner_id
        )
        review_repo = self._review_repo_factory(session)
        review = await review_repo.get_review_run_scoped(
            review_run_id, project_id, actor.owner_id
        )
        if (
            run is None
            or run.project_id != project_id
            or run.run_type != RunType.REVIEW.value
            or review is None
        ):
            raise RunNotFoundError(review_run_id)
        return run, review_repo

    async def _append_parent_event(
        self,
        session: TSession,
        run: Run,
        event_type: str,
        correlation_id: str,
        payload: dict,
    ) -> None:
        changed = await self._run_repo_factory(session).update_status(
            run.run_id,
            run.status,
            run.status,
            run.event_sequence + 1,
        )
        if not changed:
            raise RuntimeError("review_run_event_sequence_conflict")
        await self._event_repo_factory(session).add(
            create_event(
                run_id=run.run_id,
                sequence=run.event_sequence,
                event_type=event_type,
                actor_type="system",
                correlation_id=correlation_id,
                payload=payload,
            )
        )


def _metadata_snapshot(paper: ArxivPaper) -> dict:
    return {
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": list(paper.authors),
        "categories": list(paper.categories),
        "published_at": paper.published_at.isoformat(),
        "updated_at": paper.updated_at.isoformat(),
        "pdf_url": paper.pdf_url,
    }


def _cache_storage_key(owner_id: str, content_hash: str) -> str:
    """生成 owner 隔离且只含 SHA-256 的内容寻址缓存 key。"""
    return f"{owner_id}/arxiv-cache/sha256/{content_hash}.pdf"


def _search_idempotency_key(query: ArxivSearchQuery) -> str:
    payload = json.dumps(
        {
            "expression": query.expression,
            "max_results": query.max_results,
            "start": query.start,
            "sort_by": query.sort_by.value,
            "sort_order": query.sort_order.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"arxiv-search:{hashlib.sha256(payload.encode()).hexdigest()}"
