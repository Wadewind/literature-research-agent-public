"""Conversation 应用服务（切片 8）。

覆盖会话的创建、查询与提问提交：

- 创建：校验 Project 归属与归档状态、scope 合法性（
  ``selected_papers`` 要求 paper_ids 非空且全部已收录、未归档、
  属当前 owner），并解析固化默认范围 ``{paper_id, version_id}``；
- 提问提交（``post_message``）：幂等键重放 → 归档/busy/索引就绪
  校验 → User Message + rag_answer Run（含版本范围快照）+
  ``run_created`` Event + Outbox + 幂等记录在同一事务提交；
  单活跃 Run 用 ``active_run_id`` 条件更新认领，并发双发只有
  一个成功；对终态 Run 的残留认领做自愈式清理（覆盖 Run 未
  经执行器到达终态的路径，如 QUEUED 状态被直接取消）；
- 消息查询：assistant 消息携带 Claim 与 Evidence 摘要，供前端
  直接渲染引用。
"""

import hashlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.claim_set_repository import ClaimSetRepository
from literature_agent.application.ports.conversation_repository import (
    ConversationRepository,
)
from literature_agent.application.ports.event_notifier import (
    EventNotifier,
    NoopEventNotifier,
)
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRepository,
)
from literature_agent.application.ports.message_repository import MessageRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.project_paper_repository import (
    ProjectPaperRepository,
)
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.conversation import (
    Conversation,
    ConversationScopePaper,
    Message,
    MessageRole,
    ScopeMode,
    create_conversation,
    create_message,
    create_scope_paper,
    derive_title,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.evidence import RUN_INPUT_VERSION_SCOPE_KEY, Evidence
from literature_agent.domain.exceptions import (
    ConversationBusyError,
    ConversationNotFoundError,
    EvidenceNotFoundError,
    IdempotencyConflictError,
    InvalidScopeError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ProjectNotIndexedError,
)
from literature_agent.domain.queue_outbox import create_outbox_entry
from literature_agent.domain.run import RunStatus, RunType, create_run

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)

_IDEMPOTENCY_KEY_MAX_LENGTH = 255
# Run 的终态集合：残留认领指向终态 Run 时可自愈清理
_TERMINAL_STATUSES = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


@dataclass(frozen=True, slots=True)
class ConversationView:
    """会话详情视图（含固化的默认范围）。"""

    conversation: Conversation
    scope_papers: list[ConversationScopePaper]


@dataclass(frozen=True, slots=True)
class PostMessageResult:
    """提交提问的结果。"""

    user_message_id: str
    run_id: str
    status: str


@dataclass(frozen=True, slots=True)
class CitationView:
    """一条引用的前端渲染视图（Evidence 摘要）。"""

    evidence_id: str
    paper_id: str
    version_id: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    excerpt: str


@dataclass(frozen=True, slots=True)
class ClaimView:
    """一条 Claim 的前端渲染视图（文本 + 引用摘要）。"""

    text: str
    citations: list[CitationView]


@dataclass(frozen=True, slots=True)
class MessageView:
    """一条消息的前端渲染视图；assistant 消息携带 Claim 与引用。"""

    message: Message
    claims: list[ClaimView] | None


class ConversationService[TSession: Session]:
    """Conversation 用例层。

    不变量:
        - 所有查询维持 owner 隔离，越权/不存在统一 404；
        - 提问提交是「User Message + Run + Event + Outbox + 幂等记录」
          的原子事务；单活跃 Run 由 ``active_run_id`` 条件更新兜底；
        - Run ``input_payload`` 固化提交时刻解析的版本范围快照，
          后续移出/换版/归档不影响本次 Run；
        - 范围内无任何 ready ChunkSet 时快速失败
          （``project_not_indexed``），部分就绪不阻塞。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        conversation_repo_factory: Callable[[TSession], ConversationRepository],
        message_repo_factory: Callable[[TSession], MessageRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        project_paper_repo_factory: Callable[[TSession], ProjectPaperRepository],
        idempotency_repo_factory: Callable[[TSession], IdempotencyRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        claim_set_repo_factory: Callable[[TSession], ClaimSetRepository],
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        event_notifier: EventNotifier | None = None,
    ) -> None:
        """初始化 ConversationService（全部依赖为 Repository 工厂）。"""
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._conversation_repo_factory = conversation_repo_factory
        self._message_repo_factory = message_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._project_paper_repo_factory = project_paper_repo_factory
        self._idempotency_repo_factory = idempotency_repo_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._claim_set_repo_factory = claim_set_repo_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def create_conversation(
        self,
        actor: ActorContext,
        project_id: str,
        *,
        title: str | None,
        scope_mode: str,
        paper_ids: list[str] | None,
    ) -> ConversationView:
        """创建 Conversation 并解析固化默认范围。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
            ProjectArchivedError: Project 已归档。
            InvalidScopeError: scope_mode 非法、project 模式携带
                paper_ids、selected_papers 为空或含未收录/已归档/
                其他 owner 的 Paper。
        """
        async with self._session_factory() as session:
            project = await self._load_project(session, actor, project_id)
            if project.is_archived:
                raise ProjectArchivedError(project_id)

            try:
                mode = ScopeMode(scope_mode)
            except ValueError:
                raise InvalidScopeError(f"非法 scope_mode: {scope_mode}") from None

            scope_entries: list[ConversationScopePaper] = []
            conversation = create_conversation(
                project_id=project_id,
                owner_id=actor.owner_id,
                title=title,
                scope_mode=mode,
            )
            if mode is ScopeMode.PROJECT:
                if paper_ids:
                    raise InvalidScopeError("project 模式不接受 paper_ids")
            else:
                scope_entries = await self._resolve_selected_scope(
                    session, actor, project_id, conversation.conversation_id, paper_ids
                )

            conversation_repo = self._conversation_repo_factory(session)
            await conversation_repo.add(conversation)
            await session.flush()
            await conversation_repo.add_scope_papers(scope_entries)
            await session.commit()
        return ConversationView(conversation=conversation, scope_papers=scope_entries)

    async def _resolve_selected_scope(
        self,
        session: TSession,
        actor: ActorContext,
        project_id: str,
        conversation_id: str,
        paper_ids: list[str] | None,
    ) -> list[ConversationScopePaper]:
        """解析 selected_papers 模式的默认范围并固化版本。

        异常:
            InvalidScopeError: paper_ids 为空，或含未收录该 Project、
                已归档、其他 owner 的 Paper。
        """
        if not paper_ids:
            raise InvalidScopeError("selected_papers 模式要求 paper_ids 非空")
        relation_repo = self._project_paper_repo_factory(session)
        paper_repo = self._paper_repo_factory(session)
        entries: list[ConversationScopePaper] = []
        # 防御性去重（保持首次出现顺序），复合主键兜底
        for paper_id in dict.fromkeys(paper_ids):
            relation = await relation_repo.get(project_id, paper_id)
            paper = await paper_repo.get_by_id(paper_id)
            if (
                relation is None
                or paper is None
                or paper.owner_id != actor.owner_id
                or paper.is_archived
            ):
                raise InvalidScopeError(
                    f"Paper {paper_id} 不在 Project {project_id} 的可用范围内"
                )
            entries.append(
                create_scope_paper(
                    conversation_id, paper_id, relation.selected_version_id
                )
            )
        return entries

    async def list_conversations(
        self,
        actor: ActorContext,
        project_id: str,
    ) -> list[Conversation]:
        """列出 Project 的会话（owner 隔离，归档 Project 仍可读）。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            await self._load_project(session, actor, project_id)
            return await self._conversation_repo_factory(session).list_by_project(
                project_id
            )

    async def get_conversation(
        self,
        actor: ActorContext,
        conversation_id: str,
    ) -> ConversationView:
        """获取会话详情（含固化的默认范围）。

        异常:
            ConversationNotFoundError: 会话不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            conversation = await self._load_conversation(
                session, actor, conversation_id
            )
            scope = await self._conversation_repo_factory(
                session
            ).list_scope_papers(conversation_id)
            return ConversationView(conversation=conversation, scope_papers=scope)

    async def post_message(
        self,
        actor: ActorContext,
        conversation_id: str,
        *,
        content: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> PostMessageResult:
        """提交提问：User Message + rag_answer Run + Event + Outbox 原子提交。

        异常:
            ConversationNotFoundError: 会话不存在或不属于当前 actor。
            ProjectArchivedError: 所属 Project 已归档。
            ConversationBusyError: 会话已有未完成的回答 Run。
            ProjectNotIndexedError: 范围内无任何 ready ChunkSet。
            IdempotencyConflictError: 相同幂等键对应不同请求。
            ValueError: 内容或幂等键非法。
        """
        if (
            not idempotency_key
            or len(idempotency_key) > _IDEMPOTENCY_KEY_MAX_LENGTH
        ):
            raise ValueError("Idempotency-Key 不能为空且长度不得超过 255")
        if not content.strip():
            raise ValueError("提问内容不能为空")
        request_hash = self._compute_request_hash(
            conversation_id, idempotency_key, content
        )

        async with self._session_factory() as session:
            idempotency_repo = self._idempotency_repo_factory(session)
            existing = await idempotency_repo.get(actor.owner_id, idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError(idempotency_key)
                return await self._replay_result(session, existing)

            conversation_repo = self._conversation_repo_factory(session)
            conversation = await self._load_conversation(
                session, actor, conversation_id
            )
            project = await self._load_project(
                session, actor, conversation.project_id
            )
            if project.is_archived:
                raise ProjectArchivedError(project.project_id)

            # 单活跃 Run：认领指向未终态 Run 时直接拒绝（在任何副作用
            # 发生之前）；残留认领指向终态（或已消失）Run 时自愈清理，
            # 覆盖未经执行器到达终态的路径（如 QUEUED 被直接取消）。
            # 后面的 try_claim_active_run 仅作为并发竞争的 SQL 兜底。
            if conversation.active_run_id is not None:
                active_run = await self._run_repo_factory(session).get_by_id(
                    conversation.active_run_id
                )
                if (
                    active_run is not None
                    and active_run.status not in _TERMINAL_STATUSES
                ):
                    raise ConversationBusyError(conversation_id)
                await conversation_repo.release_active_run(
                    conversation_id,
                    expected_run_id=conversation.active_run_id,
                )

            # 范围快照在提交这一刻解析固化：project 模式取当前收录的
            # 全部 selected_version；selected_papers 取创建时固化的范围
            snapshot = await self._resolve_snapshot(session, conversation)
            version_ids = [entry["version_id"] for entry in snapshot]
            ready_count = await self._chunk_set_repo_factory(
                session
            ).count_ready_by_version_ids(version_ids)
            if ready_count == 0:
                raise ProjectNotIndexedError(conversation.project_id)

            message_repo = self._message_repo_factory(session)
            sequence = await message_repo.max_sequence(conversation_id) + 1
            user_message = create_message(
                conversation_id=conversation_id,
                sequence=sequence,
                role=MessageRole.USER,
                content=content,
            )
            run = create_run(
                project_id=conversation.project_id,
                owner_id=actor.owner_id,
                run_type=RunType.RAG_ANSWER,
                input_payload={
                    "conversation_id": conversation_id,
                    "user_message_id": user_message.message_id,
                    RUN_INPUT_VERSION_SCOPE_KEY: snapshot,
                },
            )
            # run_created（sequence=1）同事务写入，Run 的 event_sequence
            # 随之推进到 2（与 RunService.create_run 语义一致）
            run = replace(run, event_sequence=2)
            user_message = Message(
                message_id=user_message.message_id,
                conversation_id=user_message.conversation_id,
                sequence=user_message.sequence,
                role=user_message.role,
                content=user_message.content,
                run_id=run.run_id,
                claim_set_id=None,
                created_at=user_message.created_at,
            )

            # 先落 Run（active_run_id / message.run_id 的 FK 目标），
            # 再条件更新认领活跃 Run；认领失败说明并发提问已占用
            run_repo = self._run_repo_factory(session)
            await run_repo.add(run)
            await session.flush()
            claimed = await conversation_repo.try_claim_active_run(
                conversation_id, run.run_id
            )
            if not claimed:
                raise ConversationBusyError(conversation_id)

            await message_repo.add(user_message)
            await self._event_repo_factory(session).add(
                create_event(
                    run_id=run.run_id,
                    sequence=1,
                    event_type="run_created",
                    actor_type="user",
                    correlation_id=correlation_id,
                    payload={"status": run.status.value},
                )
            )
            await self._outbox_repo_factory(session).add(
                create_outbox_entry(run.run_id)
            )
            if conversation.title is None:
                await conversation_repo.set_title_if_null(
                    conversation_id, derive_title(content)
                )
            await idempotency_repo.add(
                IdempotencyRecord(
                    owner_id=actor.owner_id,
                    idempotency_key=idempotency_key,
                    project_id=conversation.project_id,
                    request_hash=request_hash,
                    run_id=run.run_id,
                    status="queued",
                )
            )
            await session.commit()

        await notify_run_event(self._event_notifier, run.run_id)
        return PostMessageResult(
            user_message_id=user_message.message_id,
            run_id=run.run_id,
            status="queued",
        )

    async def list_messages(
        self,
        actor: ActorContext,
        conversation_id: str,
    ) -> list[MessageView]:
        """列出会话消息（sequence 升序）；assistant 消息携带引用摘要。

        异常:
            ConversationNotFoundError: 会话不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            await self._load_conversation(session, actor, conversation_id)
            message_repo = self._message_repo_factory(session)
            messages = await message_repo.list_by_conversation(conversation_id)
            claim_set_repo = self._claim_set_repo_factory(session)
            evidence_repo = self._evidence_repo_factory(session)

            views: list[MessageView] = []
            for message in messages:
                if (
                    message.role is not MessageRole.ASSISTANT
                    or message.claim_set_id is None
                ):
                    views.append(MessageView(message=message, claims=None))
                    continue
                claims = await claim_set_repo.list_claims(message.claim_set_id)
                citations_by_claim = {
                    claim.claim_id: await claim_set_repo.list_citations(claim.claim_id)
                    for claim in claims
                }
                evidence_by_id = {
                    e.evidence_id: e
                    for e in await evidence_repo.list_by_ids(
                        [
                            c.evidence_id
                            for citations in citations_by_claim.values()
                            for c in citations
                        ]
                    )
                }
                claim_views = [
                    ClaimView(
                        text=claim.text,
                        citations=[
                            self._citation_view(evidence_by_id[c.evidence_id])
                            for c in citations_by_claim[claim.claim_id]
                            if c.evidence_id in evidence_by_id
                        ],
                    )
                    for claim in claims
                ]
                views.append(MessageView(message=message, claims=claim_views))
            return views

    async def get_evidence(
        self,
        actor: ActorContext,
        project_id: str,
        evidence_id: str,
    ) -> Evidence:
        """查询 Evidence 详情（含 excerpt 与 version_id，供 PDF 跳转）。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
            EvidenceNotFoundError: Evidence 不存在或不属于该 Project。
        """
        async with self._session_factory() as session:
            await self._load_project(session, actor, project_id)
            found = await self._evidence_repo_factory(session).list_by_ids(
                [evidence_id]
            )
            if not found or found[0].project_id != project_id:
                raise EvidenceNotFoundError(evidence_id)
            return found[0]

    async def _resolve_snapshot(
        self,
        session: TSession,
        conversation: Conversation,
    ) -> list[dict[str, str]]:
        """解析提交时刻的版本范围快照 ``[{paper_id, version_id}, ...]``。"""
        if conversation.scope_mode is ScopeMode.PROJECT:
            relations = await self._project_paper_repo_factory(
                session
            ).list_by_project(conversation.project_id)
            return [
                {"paper_id": r.paper_id, "version_id": r.selected_version_id}
                for r in relations
            ]
        scope = await self._conversation_repo_factory(session).list_scope_papers(
            conversation.conversation_id
        )
        return [
            {"paper_id": entry.paper_id, "version_id": entry.version_id}
            for entry in scope
        ]

    async def _replay_result(
        self,
        session: TSession,
        existing: IdempotencyRecord,
    ) -> PostMessageResult:
        """幂等重放：从已存记录与 User Message 回读结果，不产生新写。"""
        if existing.run_id is None:
            raise ConversationNotFoundError("missing-idempotency-run")
        user_message = await self._message_repo_factory(
            session
        ).get_by_run_and_role(existing.run_id, MessageRole.USER)
        if user_message is None:
            raise ConversationNotFoundError("missing-idempotency-message")
        return PostMessageResult(
            user_message_id=user_message.message_id,
            run_id=existing.run_id,
            status=existing.status,
        )

    async def _load_project(
        self,
        session: TSession,
        actor: ActorContext,
        project_id: str,
    ):
        """加载 Project 并校验 owner。

        异常:
            ProjectNotFoundError: 不存在或不属于当前 actor。
        """
        project = await self._project_repo_factory(session).get_by_id(project_id)
        if project is None or project.owner_id != actor.owner_id:
            raise ProjectNotFoundError(project_id)
        return project

    async def _load_conversation(
        self,
        session: TSession,
        actor: ActorContext,
        conversation_id: str,
    ) -> Conversation:
        """加载 Conversation 并校验 owner。

        异常:
            ConversationNotFoundError: 不存在或不属于当前 actor。
        """
        conversation = await self._conversation_repo_factory(session).get_by_id(
            conversation_id
        )
        if conversation is None or conversation.owner_id != actor.owner_id:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    @staticmethod
    def _citation_view(evidence: Evidence) -> CitationView:
        """组装一条引用的渲染视图。"""
        return CitationView(
            evidence_id=evidence.evidence_id,
            paper_id=evidence.paper_id,
            version_id=evidence.version_id,
            section_path=evidence.section_path,
            page_start=evidence.page_start,
            page_end=evidence.page_end,
            excerpt=evidence.excerpt,
        )

    @staticmethod
    def _compute_request_hash(
        conversation_id: str,
        idempotency_key: str,
        content: str,
    ) -> str:
        """计算提问请求指纹，用于幂等冲突检测。"""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        payload = f"{conversation_id}:{idempotency_key}:{content_hash}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
