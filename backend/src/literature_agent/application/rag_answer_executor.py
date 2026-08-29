"""RAG 回答执行器：把一次 rag_answer Run 推进到终态（切片 8）。

流程（模型调用全部在数据库事务外，结构与 ``IndexingExecutor`` 同构）：

1. 事务 A：持锁读 Run，取消检查；读 User Message，写
   ``retrieval_started``；
2. Retrieval：按 Run 固化的版本范围快照检索（快照语义优先，不依赖
   当前收录关系）；取消检查在 Retrieval 后；
3. 事务 B：写 ``retrieval_completed``（候选计数摘要）；零结果直接走
   ``insufficient_evidence`` 的最终提交（业务成功路径，不调模型）；
4. EvidenceService.commit_evidence 固化可引用 Evidence（幂等）；
5. 事务 C：取消检查，写 ``model_generation_started``；事务外经
   ModelGateway 调 ChatModel（json_schema 结构化输出，max_tokens 受
   ``AGENT_ANSWER_MAX_OUTPUT_TOKENS`` 限制）→ 解析 → Citation
   Validator；解析或校验失败修复重试一次（失败原因作为反馈消息追加
   后重新生成一次），仍失败 → Run FAILED（``model_output_invalid``）；
6. 事务 D：写 ``model_generation_completed`` 与
   ``citation_validation_completed``（只含计数与 reason code，不含
   文本）；
7. 最终事务：Assistant Message + ClaimSet + Claims + Citations +
   清 ``active_run_id`` + Run SUCCEEDED + ``answer_committed`` 原子
   提交；``claim_sets.run_id`` 唯一约束兜底重复提交（已有 ClaimSet
   则回读幂等完成，不重复创建 Message）。

取消检查分布在 Retrieval 后与模型调用前后；FAILED/CANCELLED 终态
同样清理 ``active_run_id``（否则会话永久 busy）。Context Token
Budget（2026-08-21 定稿）：证据上下文沿用检索预算截断结果
（≤ retrieval_token_budget），模板+证据超预算时按 rank 从低到高
丢弃 Evidence 缩减一次，不循环压缩。
"""

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.evidence_service import EvidenceService
from literature_agent.application.failure_policy import (
    RunFailureOutcome,
    apply_run_failure,
)
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.application.ports.claim_set_repository import ClaimSetRepository
from literature_agent.application.ports.conversation_repository import (
    ConversationRepository,
)
from literature_agent.application.ports.event_notifier import (
    EventNotifier,
    NoopEventNotifier,
)
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.message_repository import MessageRepository
from literature_agent.application.ports.outbox_repository import OutboxRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.retriever import Retriever
from literature_agent.domain.answer_schema import (
    RagAnswerOutput,
    parse_rag_answer_output,
    rag_answer_json_schema,
)
from literature_agent.domain.citation_validator import (
    CitationValidationResult,
    validate_citations,
)
from literature_agent.domain.conversation import MessageRole, create_message
from literature_agent.domain.event import create_event
from literature_agent.domain.evidence import (
    RUN_INPUT_VERSION_SCOPE_KEY,
    AnswerStatus,
    Citation,
    Evidence,
    create_claim,
    create_claim_set,
)
from literature_agent.domain.exceptions import (
    AnswerOutputParseError,
    ModelOutputInvalidError,
    RagAnswerInputError,
    RunConcurrentModificationError,
)
from literature_agent.domain.model_types import ChatMessage
from literature_agent.domain.run import Run, RunStatus, RunType
from literature_agent.domain.tokenization import count_tokens

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_MAX_LENGTH = 500
# 修复重试时回传给模型的非法输出截断长度
_REPAIR_FEEDBACK_OUTPUT_MAX_CHARS = 1000
# 证据不足时 Assistant Message 的固定回答文本（无 Claim 可拼装）
INSUFFICIENT_EVIDENCE_TEXT = "当前文献范围内证据不足，无法可靠回答该问题。"

_SYSTEM_PROMPT = (
    "你是学术文献问答助手。只允许使用给定证据回答问题，不得编造来源。\n"
    "输出 JSON：{\"answer_status\": \"answered\" | \"insufficient_evidence\", "
    "\"claims\": [{\"text\": ..., \"evidence_ids\": [...]}]}。\n"
    "answered 时每个段落级 Claim 必须至少绑定一个证据 ID；"
    "证据不足时 answer_status 为 insufficient_evidence 且 claims 为空。"
)


@dataclass(frozen=True, slots=True)
class _GenerationOutcome:
    """一次结构化生成 + 校验的结果。"""

    output: RagAnswerOutput | None
    failure_feedback: str | None
    failure_reasons: dict[str, int]
    prompt_tokens: int | None
    completion_tokens: int | None


class RagAnswerExecutor[TSession: Session]:
    """rag_answer Run 执行器，由 RunDispatcher 分发调用。

    不变量:
        - 模型调用与 Prompt 组装发生在数据库事务外；
        - 每次模型调用经 ModelGateway 记录（含 run_id）；
        - 最终 Message、Claim、Citation、清 ``active_run_id``、Run
          终态与 ``answer_committed`` 事件在同一事务提交；
        - 事件 payload 不含问题文本、回答文本或证据摘录；
        - 任何终态（SUCCEEDED/FAILED/CANCELLED）都清理会话的
          ``active_run_id``。
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        conversation_repo_factory: Callable[[TSession], ConversationRepository],
        message_repo_factory: Callable[[TSession], MessageRepository],
        claim_set_repo_factory: Callable[[TSession], ClaimSetRepository],
        attempt_repo_factory: Callable[[TSession], AttemptRepository],
        outbox_repo_factory: Callable[[TSession], OutboxRepository],
        retriever: Retriever[TSession],
        evidence_service: EvidenceService[TSession],
        model_gateway: ModelGateway[TSession],
        answer_max_output_tokens: int = 4096,
        context_token_budget: int = 3000,
        max_run_attempts: int = 3,
        event_notifier: EventNotifier | None = None,
        tokenizer: str = "cl100k_base",
    ) -> None:
        """初始化 RagAnswerExecutor。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            conversation_repo_factory: 根据 session 创建
                ConversationRepository 的工厂（清理活跃 Run 认领）。
            message_repo_factory: 根据 session 创建 MessageRepository 的工厂。
            claim_set_repo_factory: 根据 session 创建 ClaimSetRepository 的工厂。
            attempt_repo_factory: 根据 session 创建 AttemptRepository 的工厂。
            outbox_repo_factory: 根据 session 创建 OutboxRepository 的工厂。
            retriever: 快照检索编排服务。
            evidence_service: Evidence 固化服务。
            model_gateway: 模型调用入口（统一计时与调用记录）。
            answer_max_output_tokens: ChatModel 输出 token 上限。
            context_token_budget: 模板 + 证据上下文的 token 预算；
                超预算按 rank 从低到高丢弃 Evidence 缩减一次。
            max_run_attempts: 最大执行尝试次数（含首次）。
            event_notifier: 事件通知器，默认 Noop。
            tokenizer: 上下文预算使用的 tokenizer，必须与活动 ChunkProfile 一致。
        """
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._conversation_repo_factory = conversation_repo_factory
        self._message_repo_factory = message_repo_factory
        self._claim_set_repo_factory = claim_set_repo_factory
        self._attempt_repo_factory = attempt_repo_factory
        self._outbox_repo_factory = outbox_repo_factory
        self._retriever = retriever
        self._evidence_service = evidence_service
        self._model_gateway = model_gateway
        self._answer_max_output_tokens = answer_max_output_tokens
        self._context_token_budget = context_token_budget
        self._max_run_attempts = max_run_attempts
        self._event_notifier = event_notifier or NoopEventNotifier()
        self._tokenizer = tokenizer

    async def execute(self, run: Run, correlation_id: str) -> None:
        """执行一次 rag_answer Run，自行推进终态。

        参数:
            run: 已认领的 RUNNING 状态 Run。
            correlation_id: 关联标识符。
        """
        # 防御：dispatcher 已按 run_type 分发，这里兜底双保险
        if run.run_type != RunType.RAG_ANSWER.value:
            raise ValueError(f"RagAnswerExecutor 收到非 rag_answer Run: {run.run_type}")
        conversation_id, question, version_scope = await self._load_input(run)

        # 事务 A：取消检查 + retrieval_started
        if not await self._begin_retrieval(run, correlation_id):
            return  # 已取消

        # 快照检索（模型调用与只读检索均不在写事务内）
        try:
            results = await self._retriever.retrieve_for_scope(
                owner_id=run.owner_id,
                query=question,
                version_scope=version_scope,
                run_id=run.run_id,
            )
        except Exception as exc:
            logger.warning("快照检索失败: run_id=%s", run.run_id, exc_info=True)
            await self._apply_failure(run, conversation_id, exc, correlation_id)
            return

        # 事务 B：Retrieval 后取消检查 + retrieval_completed
        if not await self._complete_retrieval(run, len(results), correlation_id):
            return  # 已取消

        if not results:
            # 零候选：不调模型，直接提交证据不足回答（业务成功路径）
            await self._commit_answer(
                run,
                conversation_id,
                answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                output=None,
                correlation_id=correlation_id,
            )
            return

        # 固化 Evidence（独立短事务，幂等；快照外结果属永久错误）
        try:
            evidence = await self._evidence_service.commit_evidence(
                run=run, retrieval_results=results
            )
        except Exception as exc:
            logger.warning("Evidence 固化失败: run_id=%s", run.run_id, exc_info=True)
            await self._apply_failure(run, conversation_id, exc, correlation_id)
            return

        # 事务 C：模型调用前取消检查 + model_generation_started
        if not await self._begin_generation(run, correlation_id):
            return  # 已取消

        messages = self._build_messages(question, evidence)
        outcome = await self._generate_and_validate(
            run, conversation_id, messages, evidence, correlation_id
        )
        if outcome is None:
            return  # 模型调用失败，终态已推进
        if outcome.output is None:
            # 修复重试一次后仍非法：稳定 FAILED（model_output_invalid）
            await self._fail_invalid_output(
                run, conversation_id, outcome, correlation_id
            )
            return

        # 事务 D：生成与校验摘要事件
        if not await self._record_generation_events(run, outcome, correlation_id):
            return  # 已取消

        # 最终事务：回答产物 + 清认领 + Run 终态原子提交
        await self._commit_answer(
            run,
            conversation_id,
            answer_status=outcome.output.answer_status,
            output=outcome.output,
            correlation_id=correlation_id,
        )

    async def _load_input(self, run: Run) -> tuple[str, str, list[tuple[str, str]]]:
        """解析 Run 输入并读取 User Message 内容。

        异常:
            RagAnswerInputError: 输入缺失/形状非法或 User Message 不存在
                （永久错误，直接 FAILED）。
        """
        conversation_id = run.input_payload.get("conversation_id", "")
        user_message_id = run.input_payload.get("user_message_id", "")
        if not conversation_id or not user_message_id:
            raise RagAnswerInputError(
                f"rag_answer Run {run.run_id} 缺少 conversation_id/user_message_id"
            )
        raw_scope = run.input_payload.get(RUN_INPUT_VERSION_SCOPE_KEY)
        if not isinstance(raw_scope, list):
            raise RagAnswerInputError(f"rag_answer Run {run.run_id} 缺少版本范围快照")
        version_scope: list[tuple[str, str]] = []
        for entry in raw_scope:
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) for key in ("paper_id", "version_id")
            ):
                raise RagAnswerInputError(
                    f"rag_answer Run {run.run_id} 的版本范围快照条目形状非法"
                )
            version_scope.append((entry["paper_id"], entry["version_id"]))
        async with self._session_factory() as session:
            message = await self._message_repo_factory(session).get_by_id(
                user_message_id
            )
        if message is None or message.conversation_id != conversation_id:
            raise RagAnswerInputError(
                f"rag_answer Run {run.run_id} 的 User Message {user_message_id} 不存在"
            )
        return conversation_id, message.content, version_scope

    async def _begin_retrieval(self, run: Run, correlation_id: str) -> bool:
        """事务 A：取消检查并写 retrieval_started；取消时返回 False。"""
        async with self._session_factory() as session:
            run_row = await self._lock_run(session, run)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return False
            await self._emit_progress(
                session, run_row, "retrieval_started", {}, correlation_id
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return True

    async def _complete_retrieval(
        self,
        run: Run,
        candidate_count: int,
        correlation_id: str,
    ) -> bool:
        """事务 B：Retrieval 后取消检查并写 retrieval_completed。"""
        async with self._session_factory() as session:
            run_row = await self._lock_run(session, run)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return False
            await self._emit_progress(
                session,
                run_row,
                "retrieval_completed",
                {"candidate_count": candidate_count},
                correlation_id,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return True

    async def _begin_generation(self, run: Run, correlation_id: str) -> bool:
        """事务 C：模型调用前取消检查并写 model_generation_started。"""
        async with self._session_factory() as session:
            run_row = await self._lock_run(session, run)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return False
            await self._emit_progress(
                session, run_row, "model_generation_started", {}, correlation_id
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return True

    def _build_messages(
        self,
        question: str,
        evidence: Sequence[Evidence],
    ) -> list[ChatMessage]:
        """组装 ChatModel 上下文消息。

        证据上下文已是检索预算截断结果；若模板 + 证据总 token 超过
        ``context_token_budget``，按 Evidence 固化顺序（即检索 rank）
        从低到高丢弃，缩减一次，不循环压缩。
        """
        kept = list(evidence)
        while kept and self._context_tokens(question, kept) > self._context_token_budget:
            kept.pop()  # 丢弃排名最低的 Evidence
        blocks = [
            f"[evidence_id={e.evidence_id}] (paper_id={e.paper_id}, "
            f"pages={e.page_start}-{e.page_end}, section={e.section_path})\n"
            f"{e.excerpt}"
            for e in kept
        ]
        user_content = f"问题：\n{question}\n\n证据：\n" + "\n\n".join(blocks)
        return [
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_content),
        ]

    def _context_tokens(self, question: str, evidence: Sequence[Evidence]) -> int:
        """估算模板 + 问题 + 证据上下文的总 token 数。"""
        total = count_tokens(self._tokenizer, _SYSTEM_PROMPT) + count_tokens(
            self._tokenizer, question
        )
        for e in evidence:
            # 每条证据的开销含定位元信息行
            total += count_tokens(self._tokenizer, e.excerpt) + 30
        return total

    async def _generate_and_validate(
        self,
        run: Run,
        conversation_id: str,
        messages: list[ChatMessage],
        evidence: Sequence[Evidence],
        correlation_id: str,
    ) -> _GenerationOutcome | None:
        """调用模型生成结构化输出并校验；非法输出修复重试一次。

        返回 None 表示模型调用失败（终态已在内部推进）。
        ``output=None`` 表示修复重试后仍非法。
        """
        last_usage: tuple[int | None, int | None] = (None, None)
        repair_attempted = False
        current_messages = messages
        while True:
            try:
                result = await self._model_gateway.generate(
                    current_messages,
                    json_schema=rag_answer_json_schema(),
                    max_tokens=self._answer_max_output_tokens,
                    run_id=run.run_id,
                )
            except Exception as exc:
                logger.warning("Chat 调用失败: run_id=%s", run.run_id, exc_info=True)
                await self._apply_failure(run, conversation_id, exc, correlation_id)
                return None
            last_usage = (result.usage.prompt_tokens, result.usage.completion_tokens)
            validation: CitationValidationResult | None = None
            try:
                output = parse_rag_answer_output(result.content)
            except AnswerOutputParseError as exc:
                failure_feedback = f"输出不是合法 JSON 或不符合 Schema: {exc}"
            else:
                validation = validate_citations(
                    output, evidence=evidence, run_id=run.run_id
                )
                if validation.passed:
                    return _GenerationOutcome(
                        output=output,
                        failure_feedback=None,
                        failure_reasons={},
                        prompt_tokens=last_usage[0],
                        completion_tokens=last_usage[1],
                    )
                failure_feedback = "；".join(
                    sorted({f.reason.value for f in validation.failures})
                )
            if repair_attempted:
                # 修复重试后仍非法
                return _GenerationOutcome(
                    output=None,
                    failure_feedback=failure_feedback,
                    failure_reasons=self._failure_reason_counts(validation),
                    prompt_tokens=last_usage[0],
                    completion_tokens=last_usage[1],
                )
            repair_attempted = True
            # 把失败原因作为反馈消息追加后重新生成一次
            current_messages = [
                *messages,
                ChatMessage(
                    role="assistant",
                    content=result.content[:_REPAIR_FEEDBACK_OUTPUT_MAX_CHARS],
                ),
                ChatMessage(
                    role="user",
                    content=(
                        "上次输出未通过校验，请修正后重新输出合法 JSON。"
                        f"失败原因：{failure_feedback}"
                    ),
                ),
            ]

    @staticmethod
    def _failure_reason_counts(
        validation: CitationValidationResult | None,
    ) -> dict[str, int]:
        """统计校验失败的 reason code 计数（不含文本）。"""
        if validation is None:
            return {"parse_error": 1}
        counts: dict[str, int] = {}
        for failure in validation.failures:
            counts[failure.reason.value] = counts.get(failure.reason.value, 0) + 1
        return counts

    async def _fail_invalid_output(
        self,
        run: Run,
        conversation_id: str,
        outcome: _GenerationOutcome,
        correlation_id: str,
    ) -> None:
        """修复重试后仍非法：写校验事件并推进 FAILED（model_output_invalid）。"""
        async with self._session_factory() as session:
            run_row = await self._lock_run(session, run)
            if run_row.status != RunStatus.RUNNING:
                await session.commit()
                return
            await self._emit_progress(
                session,
                run_row,
                "citation_validation_completed",
                {"passed": False, "failure_reasons": outcome.failure_reasons},
                correlation_id,
            )
            await session.commit()
        exc = ModelOutputInvalidError(
            f"模型输出经修复重试后仍非法: {outcome.failure_feedback}"
        )
        await self._apply_failure(run, conversation_id, exc, correlation_id)

    async def _record_generation_events(
        self,
        run: Run,
        outcome: _GenerationOutcome,
        correlation_id: str,
    ) -> bool:
        """事务 D：写 model_generation_completed 与 citation_validation_completed。

        事件只含用量与 reason code 计数摘要，不含文本；取消时返回 False。
        """
        async with self._session_factory() as session:
            run_row = await self._lock_run(session, run)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return False
            await self._emit_progress(
                session,
                run_row,
                "model_generation_completed",
                {
                    "prompt_tokens": outcome.prompt_tokens,
                    "completion_tokens": outcome.completion_tokens,
                },
                correlation_id,
            )
            # 同事务内的第二条事件：内存中的 event_sequence 需同步推进，
            # 否则与上一条撞 (run_id, sequence) 唯一约束
            run_row = replace(run_row, event_sequence=run_row.event_sequence + 1)
            # 生成成功说明校验通过（失败路径不经过这里）
            await self._emit_progress(
                session,
                run_row,
                "citation_validation_completed",
                {"passed": True, "failure_reasons": {}},
                correlation_id,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)
        return True

    async def _commit_answer(
        self,
        run: Run,
        conversation_id: str,
        *,
        answer_status: AnswerStatus,
        output: RagAnswerOutput | None,
        correlation_id: str,
    ) -> None:
        """最终事务：回答产物 + 清活跃认领 + Run 终态原子提交。

        ``claim_sets.run_id`` 唯一约束兜底重复提交：已有 ClaimSet 时
        回读幂等完成，不重复创建 Message/Claim/Citation。
        """
        async with self._session_factory() as session:
            run_row = await self._lock_run(session, run)
            if await self._finalize_if_cancelled(session, run_row, correlation_id):
                await session.commit()
                await notify_run_event(self._event_notifier, run.run_id)
                return

            claim_set_repo = self._claim_set_repo_factory(session)
            message_repo = self._message_repo_factory(session)
            claim_set = await claim_set_repo.get_by_run_id(run.run_id)
            if claim_set is None:
                claim_set = create_claim_set(run.run_id, answer_status)
                await claim_set_repo.add_claim_set(claim_set)
                await session.flush()
                citations: list[Citation] = []
                if output is not None:
                    claims = [
                        create_claim(claim_set.claim_set_id, index, draft.text)
                        for index, draft in enumerate(output.claims, start=1)
                    ]
                    await claim_set_repo.add_claims(claims)
                    # Claim/Citation 之间无 ORM relationship，flush 不保证
                    # 表级插入顺序；显式 flush 确保 claims 先落库（FK）
                    await session.flush()
                    citations = [
                        Citation(claim_id=claim.claim_id, evidence_id=evidence_id)
                        for claim, draft in zip(claims, output.claims, strict=True)
                        for evidence_id in dict.fromkeys(draft.evidence_ids)
                    ]
                    await claim_set_repo.add_citations(citations)
                content = self._answer_content(output, answer_status)
                sequence = await message_repo.max_sequence(conversation_id) + 1
                await message_repo.add(
                    create_message(
                        conversation_id=conversation_id,
                        sequence=sequence,
                        role=MessageRole.ASSISTANT,
                        content=content,
                        run_id=run.run_id,
                        claim_set_id=claim_set.claim_set_id,
                    )
                )
            else:
                # 幂等完成：回读既有 ClaimSet，确保 Assistant Message 存在
                existing_message = await message_repo.get_by_run_and_role(
                    run.run_id, MessageRole.ASSISTANT
                )
                if existing_message is None:
                    claims = await claim_set_repo.list_claims(claim_set.claim_set_id)
                    content = (
                        "\n\n".join(c.text for c in claims)
                        if claims
                        else INSUFFICIENT_EVIDENCE_TEXT
                    )
                    sequence = await message_repo.max_sequence(conversation_id) + 1
                    await message_repo.add(
                        create_message(
                            conversation_id=conversation_id,
                            sequence=sequence,
                            role=MessageRole.ASSISTANT,
                            content=content,
                            run_id=run.run_id,
                            claim_set_id=claim_set.claim_set_id,
                        )
                    )

            # 清会话活跃认领与 Run 终态同事务提交
            await self._conversation_repo_factory(session).release_active_run(
                conversation_id, expected_run_id=run.run_id
            )
            claim_count = (
                len(await claim_set_repo.list_claims(claim_set.claim_set_id))
            )
            await self._finish_run(
                session,
                run_row,
                RunStatus.SUCCEEDED,
                "answer_committed",
                {
                    "claim_set_id": claim_set.claim_set_id,
                    "answer_status": claim_set.answer_status.value,
                    "claim_count": claim_count,
                },
                correlation_id,
            )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)

    @staticmethod
    def _answer_content(
        output: RagAnswerOutput | None,
        answer_status: AnswerStatus,
    ) -> str:
        """拼装 Assistant Message 文本：claims 段落拼接；证据不足用固定文本。"""
        if answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE or output is None:
            return INSUFFICIENT_EVIDENCE_TEXT
        return "\n\n".join(claim.text for claim in output.claims)

    async def _apply_failure(
        self,
        run: Run,
        conversation_id: str | None,
        exc: Exception,
        correlation_id: str | None,
    ) -> None:
        """失败路径：按错误分类推进 FAILED 或 RETRY_WAIT；FAILED 时清活跃认领。"""
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:_ERROR_MESSAGE_MAX_LENGTH],
        }
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            run_row = await run_repo.get_by_id_for_update(run.run_id, run.owner_id)
            if run_row is None or run_row.status != RunStatus.RUNNING:
                await session.commit()
                return
            outcome = await apply_run_failure(
                session,
                run_repo_factory=self._run_repo_factory,
                event_repo_factory=self._event_repo_factory,
                attempt_repo_factory=self._attempt_repo_factory,
                outbox_repo_factory=self._outbox_repo_factory,
                run=run_row,
                error=error,
                exc=exc,
                correlation_id=correlation_id or f"rag-answer:{run.run_id}",
                max_run_attempts=self._max_run_attempts,
            )
            if outcome is RunFailureOutcome.FAILED and conversation_id:
                # 终态 FAILED 必须清活跃认领，否则会话永久 busy；
                # RETRY_WAIT 保持认领（Run 未结束，会话仍忙）
                await self._conversation_repo_factory(session).release_active_run(
                    conversation_id, expected_run_id=run.run_id
                )
            await session.commit()
        await notify_run_event(self._event_notifier, run.run_id)

    async def _finalize_if_cancelled(
        self,
        session: TSession,
        run_row: Run,
        correlation_id: str,
    ) -> bool:
        """若 Run 已被请求取消，则推进 CANCELLED（并清活跃认领）。"""
        if run_row.status != RunStatus.CANCEL_REQUESTED:
            return False
        conversation_id = run_row.input_payload.get("conversation_id")
        if conversation_id:
            await self._conversation_repo_factory(session).release_active_run(
                conversation_id, expected_run_id=run_row.run_id
            )
        await self._finish_run(
            session, run_row, RunStatus.CANCELLED, "run_cancelled", {}, correlation_id
        )
        return True

    async def _lock_run(self, session: TSession, run: Run) -> Run:
        """持行锁读取最新 Run；不存在视为并发修改冲突。"""
        run_row = await self._run_repo_factory(session).get_by_id_for_update(
            run.run_id, run.owner_id
        )
        if run_row is None:
            raise RunConcurrentModificationError(run.run_id)
        return run_row

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
