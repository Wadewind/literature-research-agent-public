"""按 Agent Turn 授权快照执行 Project 只读研究工具。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.project_research_context import (
    READ_REVIEW_EVIDENCE_MATRIX,
    SEARCH_PROJECT_CHUNKS,
    ProjectContextToolResult,
    ProjectResearchContextError,
)
from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.tool_execution_repository import (
    ToolExecutionRepository,
)
from literature_agent.application.retriever import RetrievalResult
from literature_agent.domain.event import create_event
from literature_agent.domain.evidence import EVIDENCE_EXCERPT_MAX_CHARS, Evidence, create_evidence
from literature_agent.domain.research_agent import (
    AgentSession,
    AgentTurnRun,
    ContextSnapshot,
    PolicySnapshot,
)
from literature_agent.domain.review import ReviewOutput, ReviewOutputType
from literature_agent.domain.run import Run, RunStatus, RunType
from literature_agent.domain.tool_execution import (
    TOOL_RESULT_MAX_CHARS,
    ToolErrorKind,
    ToolExecution,
    ToolExecutionStatus,
    canonical_tool_args,
    create_tool_execution,
)

TSession = TypeVar("TSession", bound=Session)
_SEARCH_MAX_ITEMS = 8
_MATRIX_MAX_ROWS = 12
_MATRIX_SOURCE_MAX_ROWS = 60
_MATRIX_SOURCE_MAX_REFERENCES = 600
_MATRIX_SOURCE_MAX_FAILURES = 10
_DIMENSION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _Retriever(Protocol):
    async def retrieve_for_scope(self, **kwargs) -> list[RetrievalResult]: ...


@dataclass(frozen=True, slots=True)
class _Closure:
    run: Run
    agent_session: AgentSession
    turn: AgentTurnRun
    context: ContextSnapshot
    policy: PolicySnapshot


@dataclass(frozen=True, slots=True)
class _Started:
    closure: _Closure
    execution: ToolExecution
    replay: ProjectContextToolResult | None


class ProjectResearchContextService[TSession: Session]:
    """每次调用都由 turn_run_id 反查并复核不可变授权闭包。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        tool_execution_repo_factory: Callable[[TSession], ToolExecutionRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        retriever: _Retriever,
        chunk_repo_factory: Callable[[TSession], ChunkRepository] | None = None,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._agent_repo_factory = agent_repo_factory
        self._review_repo_factory = review_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._tool_repo_factory = tool_execution_repo_factory
        self._event_repo_factory = event_repo_factory
        self._retriever = retriever
        self._chunk_repo_factory = chunk_repo_factory
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def search_project_chunks(
        self, turn_run_id: str, *, query: str
    ) -> ProjectContextToolResult:
        normalized = query.strip()
        if not normalized or len(normalized) > 1_000:
            raise _error("project_context_query_invalid", "检索问题为空或超过长度限制")
        proposed = create_tool_execution(
            turn_run_id=turn_run_id,
            tool_name=SEARCH_PROJECT_CHUNKS,
            arguments={"query": normalized},
        )
        started = await self._begin(proposed)
        if started.replay is not None:
            return started.replay
        refs = started.closure.context.project_index_refs
        try:
            results = await self._retriever.retrieve_for_scope(
                owner_id=started.closure.run.owner_id,
                query=normalized,
                version_scope=[(item.paper_id, item.paper_version_id) for item in refs],
                chunk_set_scope=[item.chunk_set_id for item in refs],
                run_id=turn_run_id,
            )
            return await self._complete_search(started, results)
        except ProjectResearchContextError as exc:
            await self._fail(started, exc)
            raise
        except Exception as exc:
            error = _error(
                "project_context_retrieval_unavailable",
                "Project 检索暂时不可用",
                ToolErrorKind.TEMPORARY,
            )
            await self._fail(started, error)
            raise error from exc

    async def read_review_evidence_matrix(
        self, turn_run_id: str
    ) -> ProjectContextToolResult:
        proposed = create_tool_execution(
            turn_run_id=turn_run_id,
            tool_name=READ_REVIEW_EVIDENCE_MATRIX,
            arguments={},
        )
        started = await self._begin(proposed)
        if started.replay is not None:
            return started.replay
        try:
            output, evidence = await self._read_matrix_source(started.closure)
            return await self._complete_matrix(started, output, evidence)
        except ProjectResearchContextError as exc:
            await self._fail(started, exc)
            raise
        except Exception as exc:
            error = _error(
                "project_context_matrix_invalid",
                "Review Evidence Matrix 无法通过范围复核",
            )
            await self._fail(started, error)
            raise error from exc

    async def _begin(self, proposed: ToolExecution) -> _Started:
        emitted = False
        async with self._session_factory() as session:
            closure, locked = await self._load_closure(session, proposed.turn_run_id, lock=True)
            self._require_running(locked)
            if proposed.tool_name not in closure.policy.allowed_tool_names:
                raise _error("project_context_tool_not_allowed", "本轮未授权该 Project Tool")
            repo = self._tool_repo_factory(session)
            existing = await repo.get(proposed.effect_id)
            if existing is not None:
                if existing.status is ToolExecutionStatus.SUCCEEDED:
                    await session.commit()
                    return _Started(closure, existing, _result(existing))
                if existing.status is ToolExecutionStatus.RUNNING:
                    raise _error(
                        "project_context_effect_in_progress",
                        "相同 Tool effect 正在执行",
                        ToolErrorKind.TEMPORARY,
                    )
                if existing.error_kind is not ToolErrorKind.TEMPORARY:
                    raise ProjectResearchContextError(
                        existing.error_code or "project_context_effect_failed",
                        existing.safe_message or "相同 Tool effect 已失败",
                        existing.error_kind or ToolErrorKind.PERMANENT,
                    )
                running = existing.retry()
                if not await repo.save(
                    running,
                    expected_status=ToolExecutionStatus.FAILED,
                    expected_attempt_count=existing.attempt_count,
                ):
                    raise _error(
                        "project_context_effect_in_progress",
                        "相同 Tool effect 已被其他执行者认领",
                        ToolErrorKind.TEMPORARY,
                    )
                execution = running
            else:
                count = await repo.count_by_turn(proposed.turn_run_id)
                if count >= closure.policy.max_tool_calls:
                    raise _error("project_context_tool_budget_exceeded", "本轮 Tool 调用预算已耗尽")
                execution = await repo.add(proposed)
            await self._append_tool_event(
                session,
                locked,
                execution,
                "agent_tool_started",
                {"attempt_count": execution.attempt_count},
            )
            emitted = True
            await session.commit()
        if emitted:
            await notify_run_event(self._event_notifier, proposed.turn_run_id)
        return _Started(closure, execution, None)

    async def _complete_search(
        self, started: _Started, results: Sequence[RetrievalResult]
    ) -> ProjectContextToolResult:
        deduped = list({item.chunk.chunk_id: item for item in results}.values())
        selected = deduped[:_SEARCH_MAX_ITEMS]
        allowed = {
            (item.paper_id, item.paper_version_id, item.chunk_set_id)
            for item in started.closure.context.project_index_refs
        }
        for item in selected:
            if (item.paper_id, item.version_id, item.chunk.chunk_set_id) not in allowed:
                raise _error(
                    "project_context_retrieval_scope_mismatch",
                    "检索结果超出 ContextSnapshot",
                )
        async with self._session_factory() as session:
            closure, locked = await self._load_closure(
                session, started.execution.turn_run_id, lock=True
            )
            self._require_same_closure(started.closure, closure)
            self._require_running(locked)
            fresh: list[Evidence] = []
            for item in selected:
                chunk_set = await self._chunk_set_repo_factory(session).get_by_id(
                    item.chunk.chunk_set_id
                )
                if chunk_set is None:
                    raise _error("project_context_chunk_set_missing", "Snapshot ChunkSet 不存在")
                fresh.append(
                    create_evidence(
                        run_id=locked.run_id,
                        project_id=locked.project_id,
                        paper_id=item.paper_id,
                        version_id=item.version_id,
                        parse_revision_id=chunk_set.parse_revision_id,
                        chunk_id=item.chunk.chunk_id,
                        section_path=item.chunk.section_path,
                        page_start=item.chunk.page_start,
                        page_end=item.chunk.page_end,
                        excerpt=item.chunk.text[:EVIDENCE_EXCERPT_MAX_CHARS],
                    )
                )
            persisted = await self._evidence_repo_factory(session).get_or_add_many(fresh)
            if len(persisted) != len(fresh):
                raise _error(
                    "project_context_evidence_conflict",
                    "Agent Evidence 回读数量不一致",
                )
            for proposed, found in zip(fresh, persisted, strict=True):
                _require_same_evidence(proposed, found)
            payload = {
                "items": [
                    {
                        "evidence_id": evidence.evidence_id,
                        "paper_id": evidence.paper_id,
                        "version_id": evidence.version_id,
                        "section_path": evidence.section_path,
                        "page_start": evidence.page_start,
                        "page_end": evidence.page_end,
                        "excerpt": evidence.excerpt,
                    }
                    for evidence in persisted
                ],
                "returned_count": len(persisted),
                "truncated": len(deduped) > len(selected),
            }
            return await self._finish(session, locked, started.execution, payload)

    async def _read_matrix_source(
        self, closure: _Closure
    ) -> tuple[ReviewOutput, list[Evidence]]:
        async with self._session_factory() as session:
            current, locked = await self._load_closure(session, closure.run.run_id, lock=False)
            self._require_same_closure(closure, current)
            self._require_running(locked)
            output_id = current.context.review_output_id
            if output_id is None:
                raise _error("project_context_matrix_missing", "ContextSnapshot 未绑定 Matrix")
            output = await self._review_repo_factory(session).get_output_scoped(
                output_id, locked.project_id, locked.owner_id
            )
            if (
                output is None
                or output.output_type is not ReviewOutputType.EVIDENCE_MATRIX
                or output.output_key != "evidence-matrix"
                or output.version != 1
                or output.schema_version != "evidence-matrix.v1"
            ):
                raise _error("project_context_matrix_invalid", "Matrix 身份或版本不受支持")
            raw_ids = _validate_matrix_payload(
                output.payload,
                allowed_paper_ids={item.paper_id for item in current.context.project_index_refs},
            )
            evidence = await self._evidence_repo_factory(session).list_by_ids(raw_ids)
            if len(evidence) != len(set(raw_ids)):
                raise _error("project_context_matrix_invalid", "Matrix Evidence 不完整")
            return output, evidence

    async def _complete_matrix(
        self,
        started: _Started,
        output: ReviewOutput,
        source_evidence: list[Evidence],
    ) -> ProjectContextToolResult:
        async with self._session_factory() as session:
            closure, locked = await self._load_closure(
                session, started.execution.turn_run_id, lock=True
            )
            self._require_same_closure(started.closure, closure)
            self._require_running(locked)
            refs = {
                (item.paper_id, item.paper_version_id): item.chunk_set_id
                for item in closure.context.project_index_refs
            }
            chunks = {}
            if source_evidence:
                if self._chunk_repo_factory is None:
                    raise _error(
                        "project_context_matrix_invalid",
                        "Matrix Reader 缺少 Chunk 复核能力",
                    )
                chunks = {
                    item.chunk_id: item
                    for item in await self._chunk_repo_factory(session).list_by_ids(
                        [item.chunk_id for item in source_evidence]
                    )
                }
            for item in source_evidence:
                chunk = chunks.get(item.chunk_id)
                expected_chunk_set = refs.get((item.paper_id, item.version_id))
                if (
                    item.run_id != output.review_run_id
                    or item.project_id != locked.project_id
                    or chunk is None
                    or chunk.chunk_set_id != expected_chunk_set
                ):
                    raise _error("project_context_matrix_invalid", "Matrix Evidence 超出 Snapshot")
            rows = _select_matrix_rows(output.payload["rows"])
            selected_source_ids = {
                evidence_id for row in rows for evidence_id in row["evidence_ids"]
            }
            selected_source_evidence = [
                item for item in source_evidence if item.evidence_id in selected_source_ids
            ]
            fresh: list[Evidence] = []
            for item in selected_source_evidence:
                fresh.append(
                    create_evidence(
                        run_id=locked.run_id,
                        project_id=locked.project_id,
                        paper_id=item.paper_id,
                        version_id=item.version_id,
                        parse_revision_id=item.parse_revision_id,
                        chunk_id=item.chunk_id,
                        section_path=item.section_path,
                        page_start=item.page_start,
                        page_end=item.page_end,
                        excerpt=item.excerpt,
                    )
                )
            cloned = await self._evidence_repo_factory(session).get_or_add_many(fresh)
            if len(cloned) != len(fresh):
                raise _error("project_context_evidence_conflict", "Matrix Evidence 回读数量不一致")
            by_source = {
                source.evidence_id: target
                for source, target in zip(selected_source_evidence, cloned, strict=True)
            }
            returned: list[dict] = []
            for row in rows:
                item = {
                    "paper_id": row["paper_id"],
                    "dimension_key": row["dimension_key"],
                    "status": row["status"],
                    "finding": row["finding"],
                    "limitations": row["limitations"],
                    "evidence_ids": [by_source[value].evidence_id for value in row["evidence_ids"]],
                }
                returned.append(item)
            payload = {
                "rows": returned,
                "returned_count": len(returned),
                "truncated": len(output.payload["rows"]) > len(returned),
            }
            return await self._finish(session, locked, started.execution, payload)

    async def _finish(
        self, session: TSession, locked: Run, execution: ToolExecution, payload: dict
    ) -> ProjectContextToolResult:
        succeeded = execution.succeed(payload)
        if not await self._tool_repo_factory(session).save(
            succeeded,
            expected_status=ToolExecutionStatus.RUNNING,
            expected_attempt_count=execution.attempt_count,
        ):
            raise _error(
                "project_context_effect_conflict",
                "Tool effect 完成发生并发冲突",
                ToolErrorKind.TEMPORARY,
            )
        await self._append_tool_event(
            session,
            locked,
            succeeded,
            "agent_tool_succeeded",
            {"result_hash": succeeded.result_hash},
        )
        await session.commit()
        await notify_run_event(self._event_notifier, locked.run_id)
        return _result(succeeded)

    async def _fail(self, started: _Started, error: ProjectResearchContextError) -> None:
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(started.execution.turn_run_id)
            if run is None:
                return
            locked = await self._run_repo_factory(session).get_by_id_for_update(
                run.run_id, run.owner_id
            )
            if locked is None:
                return
            failed = started.execution.fail(error.kind, error.code, error.safe_message)
            changed = await self._tool_repo_factory(session).save(
                failed,
                expected_status=ToolExecutionStatus.RUNNING,
                expected_attempt_count=started.execution.attempt_count,
            )
            if changed and locked.status in {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}:
                await self._append_tool_event(
                    session,
                    locked,
                    failed,
                    "agent_tool_failed",
                    {"error_kind": error.kind.value, "error_code": error.code},
                )
            await session.commit()
        await notify_run_event(self._event_notifier, started.execution.turn_run_id)

    async def _load_closure(
        self, session: TSession, turn_run_id: str, *, lock: bool
    ) -> tuple[_Closure, Run]:
        run_repo = self._run_repo_factory(session)
        raw = await run_repo.get_by_id(turn_run_id)
        if raw is None:
            raise _error("project_context_scope_invalid", "Agent Turn 不存在")
        run = (
            await run_repo.get_by_id_for_update(turn_run_id, raw.owner_id) if lock else raw
        )
        if run is None or run.run_type != RunType.AGENT_TURN.value:
            raise _error("project_context_scope_invalid", "Agent Turn 作用域非法")
        agent_repo = self._agent_repo_factory(session)
        turn = await agent_repo.get_turn_scoped(run.run_id, run.owner_id)
        if turn is None:
            raise _error("project_context_scope_invalid", "Agent Turn 作用域非法")
        agent_session = await agent_repo.get_session_scoped(turn.session_id, run.owner_id)
        context = await agent_repo.get_context_snapshot(turn.context_snapshot_id)
        policy = await agent_repo.get_policy_snapshot(turn.policy_snapshot_id)
        if (
            agent_session is None
            or context is None
            or policy is None
            or agent_session.project_id != run.project_id
            or context.owner_id != run.owner_id
            or context.project_id != run.project_id
            or context.session_id != turn.session_id
            or context.turn_run_id != run.run_id
            or policy.owner_id != run.owner_id
            or policy.project_id != run.project_id
            or policy.session_id != turn.session_id
            or policy.turn_run_id != run.run_id
        ):
            raise _error("project_context_scope_invalid", "Agent Context 授权闭包非法")
        return _Closure(run, agent_session, turn, context, policy), run

    @staticmethod
    def _require_running(run: Run) -> None:
        if run.status is RunStatus.CANCEL_REQUESTED:
            raise _error(
                "project_context_cancelled",
                "Agent Turn 已请求取消",
                ToolErrorKind.CANCELLED,
            )
        if run.status is not RunStatus.RUNNING:
            raise _error("project_context_turn_not_running", "Agent Turn 当前不可调用 Tool")

    @staticmethod
    def _require_same_closure(expected: _Closure, current: _Closure) -> None:
        if (
            expected.run.run_id != current.run.run_id
            or expected.agent_session != current.agent_session
            or expected.turn != current.turn
            or expected.context != current.context
            or expected.policy != current.policy
        ):
            raise _error("project_context_scope_changed", "Agent Context 授权闭包发生变化")

    async def _append_tool_event(
        self,
        session: TSession,
        run: Run,
        execution: ToolExecution,
        event_type: str,
        extra: dict,
    ) -> None:
        payload = {
            "tool_name": execution.tool_name,
            "effect_id": execution.effect_id,
            "status": execution.status.value,
            **extra,
        }
        await self._event_repo_factory(session).add(
            create_event(
                run.run_id,
                run.event_sequence,
                event_type,
                "system",
                f"tool:{execution.effect_id}",
                payload,
            )
        )
        if not await self._run_repo_factory(session).update_status(
            run.run_id,
            run.status,
            run.status,
            run.event_sequence + 1,
        ):
            raise _error(
                "project_context_event_conflict",
                "Tool Event 序号发生并发冲突",
                ToolErrorKind.TEMPORARY,
            )


def _validate_matrix_payload(
    payload: dict, *, allowed_paper_ids: set[str]
) -> list[str]:
    if set(payload) != {"rows", "paper_failures", "summary"}:
        raise _error("project_context_matrix_invalid", "Matrix 聚合结构非法")
    rows = payload.get("rows")
    failures = payload.get("paper_failures")
    summary = payload.get("summary")
    if (
        not isinstance(rows, list)
        or not isinstance(failures, list)
        or not isinstance(summary, dict)
    ):
        raise _error("project_context_matrix_invalid", "Matrix 聚合结构非法")
    ids: list[str] = []
    seen: set[tuple[str, str]] = set()
    successful_papers: set[str] = set()
    expected_keys = {
        "paper_id",
        "dimension_key",
        "status",
        "finding",
        "limitations",
        "evidence_ids",
    }
    if len(rows) > _MATRIX_SOURCE_MAX_ROWS:
        raise _error("project_context_matrix_invalid", "Matrix 行数超过上限")
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise _error("project_context_matrix_invalid", "Matrix 行结构非法")
        paper_id = row.get("paper_id")
        dimension_key = row.get("dimension_key")
        key = (cast(str, paper_id), cast(str, dimension_key))
        evidence_ids = row.get("evidence_ids")
        if (
            not isinstance(paper_id, str)
            or not paper_id
            or len(paper_id) > 64
            or paper_id not in allowed_paper_ids
            or not isinstance(dimension_key, str)
            or not _DIMENSION_KEY_PATTERN.fullmatch(dimension_key)
            or key in seen
            or row.get("status") not in {"extracted", "insufficient_evidence"}
            or not isinstance(evidence_ids, list)
            or not all(
                isinstance(value, str) and 0 < len(value) <= 255
                for value in evidence_ids
            )
            or len(evidence_ids) != len(set(evidence_ids))
            or len(evidence_ids) > 10
            or any(
                value is not None and (not isinstance(value, str) or len(value) > 500)
                for value in (row.get("finding"), row.get("limitations"))
            )
        ):
            raise _error("project_context_matrix_invalid", "Matrix 行语义非法")
        if row["status"] == "extracted" and (
            not isinstance(row["finding"], str)
            or not row["finding"].strip()
            or not evidence_ids
        ):
            raise _error("project_context_matrix_invalid", "Matrix extracted 行语义非法")
        if row["status"] == "insufficient_evidence" and (
            row["finding"] is not None or row["limitations"] is not None or evidence_ids
        ):
            raise _error("project_context_matrix_invalid", "Matrix 证据不足行语义非法")
        seen.add(key)
        successful_papers.add(paper_id)
        ids.extend(evidence_ids)
    if len(ids) > _MATRIX_SOURCE_MAX_REFERENCES:
        raise _error("project_context_matrix_invalid", "Matrix Evidence 引用超过上限")
    if len(failures) > _MATRIX_SOURCE_MAX_FAILURES:
        raise _error("project_context_matrix_invalid", "Matrix 失败摘要超过上限")
    failed_sources: set[str] = set()
    failed_papers: set[str] = set()
    for failure in failures:
        if (
            not isinstance(failure, dict)
            or set(failure) != {"source_id", "paper_id", "error_code"}
            or not isinstance(failure.get("source_id"), str)
            or not 0 < len(failure["source_id"]) <= 64
            or not isinstance(failure.get("paper_id"), str)
            or not 0 < len(failure["paper_id"]) <= 64
            or failure["paper_id"] not in allowed_paper_ids
            or failure.get("error_code") != "evidence_matrix_invalid"
            or failure["source_id"] in failed_sources
            or failure["paper_id"] in failed_papers
            or failure["paper_id"] in successful_papers
        ):
            raise _error("project_context_matrix_invalid", "Matrix 失败摘要非法")
        failed_sources.add(failure["source_id"])
        failed_papers.add(failure["paper_id"])
    if (
        set(summary) != {"valid_papers", "failed_papers"}
        or isinstance(summary.get("valid_papers"), bool)
        or not isinstance(summary.get("valid_papers"), int)
        or isinstance(summary.get("failed_papers"), bool)
        or not isinstance(summary.get("failed_papers"), int)
        or summary["valid_papers"] != len(successful_papers)
        or summary["failed_papers"] != len(failed_papers)
    ):
        raise _error("project_context_matrix_invalid", "Matrix 统计摘要非法")
    return list(dict.fromkeys(ids))


def _select_matrix_rows(rows: list[dict]) -> list[dict]:
    """在复制 Evidence 前按稳定顺序施加行数与结果字符预算。"""
    ordered = sorted(rows, key=lambda row: (row["paper_id"], row["dimension_key"]))
    selected: list[dict] = []
    for row in ordered:
        candidate = {
            "rows": [*selected, row],
            "returned_count": len(selected) + 1,
            "truncated": len(ordered) > len(selected) + 1,
        }
        if (
            len(selected) >= _MATRIX_MAX_ROWS
            or len(canonical_tool_args(candidate)) > TOOL_RESULT_MAX_CHARS
        ):
            break
        selected.append(row)
    return selected


def _result(execution: ToolExecution) -> ProjectContextToolResult:
    if execution.result_payload is None or execution.result_hash is None:
        raise _error("project_context_result_missing", "Tool effect 结果不可用")
    return ProjectContextToolResult(
        execution.effect_id,
        execution.tool_name,
        execution.result_payload,
        execution.result_hash,
    )


def _require_same_evidence(expected: Evidence, actual: Evidence) -> None:
    if (
        expected.run_id,
        expected.project_id,
        expected.paper_id,
        expected.version_id,
        expected.parse_revision_id,
        expected.chunk_id,
        expected.section_path,
        expected.page_start,
        expected.page_end,
        expected.excerpt,
    ) != (
        actual.run_id,
        actual.project_id,
        actual.paper_id,
        actual.version_id,
        actual.parse_revision_id,
        actual.chunk_id,
        actual.section_path,
        actual.page_start,
        actual.page_end,
        actual.excerpt,
    ):
        raise _error("project_context_evidence_conflict", "Agent Evidence 幂等事实冲突")


def _error(
    code: str,
    safe_message: str,
    kind: ToolErrorKind = ToolErrorKind.PERMANENT,
) -> ProjectResearchContextError:
    return ProjectResearchContextError(code, safe_message, kind)
