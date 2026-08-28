"""Sandbox 输出校验、暂存、正式发布与下载查询。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TypeVar

from literature_agent.application.event_notification import notify_run_event
from literature_agent.application.ports.agent_artifact_source import (
    AgentArtifactSource,
    AgentArtifactSourceScope,
)
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.event_notifier import EventNotifier, NoopEventNotifier
from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.public_source_resolver import PublicSourceResolver
from literature_agent.application.ports.research_agent_runtime import RuntimeTurnRequest
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.runtime_execution_control import RuntimeExecutionControl
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage, StorageError
from literature_agent.domain.agent_artifact import (
    AGENT_ARTIFACT_MAX_BYTES,
    AgentArtifact,
    AgentArtifactValidationError,
    agent_artifact_candidate_id,
    agent_artifact_storage_key,
    is_agent_artifact_output_path,
    validate_agent_artifact_content,
    validate_agent_artifact_name_and_type,
)
from literature_agent.domain.agent_network import (
    FormalSourceValidationError,
    normalize_formal_public_source,
    validate_formal_public_source_addresses,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    AgentArtifactNotFoundError,
    AgentTurnNotFoundError,
    RunConcurrentModificationError,
)
from literature_agent.domain.research_agent import (
    AgentArtifactCandidate,
    AgentArtifactCandidateStatus,
    create_agent_artifact_candidate,
    same_agent_artifact_candidate_fact,
)
from literature_agent.domain.run import RunStatus, RunType
from literature_agent.domain.runtime_execution import RuntimeExecutionPermit

TSession = TypeVar("TSession", bound=Session)
AGENT_ARTIFACT_MAX_PER_TURN = 8
AGENT_ARTIFACT_MAX_TOTAL_BYTES_PER_TURN = 50 * 1024 * 1024


class AgentArtifactServiceError(Exception):
    """文件提交边界的稳定错误，不携带路径、正文或 Provider 详情。"""

    def __init__(self, code: str, safe_message: str, *, temporary: bool = False) -> None:
        self.code = code
        self.safe_message = safe_message
        self.temporary = temporary
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class AgentArtifactContent:
    artifact: AgentArtifact
    content: bytes


class AgentArtifactSubmissionService[TSession: Session]:
    """事务外读取 Sandbox/写 Storage，再以短事务登记 VALIDATED Candidate。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        storage: Storage,
        execution_control: RuntimeExecutionControl,
        source_resolver: PublicSourceResolver | None = None,
        event_notifier: EventNotifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._agent_repo_factory = agent_repo_factory
        self._event_repo_factory = event_repo_factory
        self._storage = storage
        self._execution_control = execution_control
        self._source_resolver = source_resolver
        self._event_notifier = event_notifier or NoopEventNotifier()

    async def submit(
        self,
        *,
        request: RuntimeTurnRequest,
        permit: RuntimeExecutionPermit,
        source: AgentArtifactSource,
        tool_call_id: str,
        path: str,
        name: str,
        media_type: str,
        source_url: str | None = None,
    ) -> AgentArtifactCandidate:
        candidate_id = agent_artifact_candidate_id(request.turn_run_id, tool_call_id)
        # 先验证 Runtime/Sandbox fence，避免已取消或过期的调用写入拒绝事实或 Storage。
        await self._assert_current(request, permit, source)
        try:
            if "submit_artifact" not in request.policy_snapshot.allowed_tool_names:
                raise AgentArtifactValidationError(
                    "artifact_tool_not_allowed", "本轮未授权提交 Artifact"
                )
            if not is_agent_artifact_output_path(path):
                raise AgentArtifactValidationError(
                    "artifact_path_invalid", "Artifact 只能来自 /workspace/outputs/"
                )
            validate_agent_artifact_name_and_type(name, media_type)
            formal_source = None
            if source_url is not None:
                try:
                    formal_source = normalize_formal_public_source(source_url)
                except FormalSourceValidationError as exc:
                    raise AgentArtifactValidationError(
                        exc.code, exc.safe_message
                    ) from exc
                if self._source_resolver is None:
                    raise AgentArtifactValidationError(
                        "source_resolution_unavailable", "声明来源目标检查暂不可用"
                    )
                try:
                    addresses = await self._source_resolver.resolve(
                        formal_source.hostname, formal_source.port
                    )
                    validate_formal_public_source_addresses(formal_source, addresses)
                except FormalSourceValidationError as exc:
                    raise AgentArtifactValidationError(
                        exc.code, exc.safe_message
                    ) from exc
                except Exception as exc:
                    raise AgentArtifactValidationError(
                        "source_resolution_failed", "来源地址解析失败"
                    ) from exc
                await self._assert_current(request, permit, source)
            content = await source.read_regular_file(path, max_bytes=AGENT_ARTIFACT_MAX_BYTES)
            validated = validate_agent_artifact_content(
                name=name, media_type=media_type, content=content
            )
            storage_key = agent_artifact_storage_key(
                owner_id=request.context_snapshot.owner_id,
                session_id=request.session_id,
                turn_run_id=request.turn_run_id,
                content_hash=validated.content_hash,
            )
            await self._assert_current(request, permit, source)
            await self._storage.write(storage_key, content)
            await self._assert_current(request, permit, source)
            candidate = create_agent_artifact_candidate(
                candidate_id=candidate_id,
                owner_id=request.context_snapshot.owner_id,
                project_id=request.context_snapshot.project_id,
                session_id=request.session_id,
                turn_run_id=request.turn_run_id,
                name=name,
                media_type=media_type,
                content_ref=path,
                content_hash=validated.content_hash,
                size_bytes=validated.size_bytes,
                source_url=formal_source.url if formal_source else None,
                source_url_hash=formal_source.source_hash if formal_source else None,
            ).validate(
                tool_call_id=tool_call_id,
                storage_key=storage_key,
                sandbox_generation=source.scope.sandbox_generation,
                sandbox_fencing_token=source.scope.sandbox_fencing_token,
            )
            return await self._record_candidate(request, source.scope, candidate)
        except AgentArtifactValidationError as exc:
            # 校验失败本身是一个业务事实，但取消/失去 fence 后不得再新增该事实。
            await self._assert_current(request, permit, source)
            await self._record_rejection(
                request=request,
                candidate_id=candidate_id,
                tool_call_id=tool_call_id,
                code=exc.code,
                name=name,
                media_type=media_type,
            )
            raise AgentArtifactServiceError(exc.code, exc.safe_message) from exc

    async def _assert_current(
        self,
        request: RuntimeTurnRequest,
        permit: RuntimeExecutionPermit,
        source: AgentArtifactSource,
    ) -> None:
        await self._execution_control.assert_active(permit)
        scope = source.scope
        if (
            scope.owner_id != request.context_snapshot.owner_id
            or scope.project_id != request.context_snapshot.project_id
            or scope.session_id != request.session_id
            or scope.turn_run_id != request.turn_run_id
        ):
            raise AgentArtifactServiceError(
                "artifact_sandbox_fence_lost",
                "Artifact Sandbox generation/fence 已失效",
            )
        await source.assert_current()

    async def _record_candidate(
        self,
        request: RuntimeTurnRequest,
        source_scope: AgentArtifactSourceScope,
        candidate: AgentArtifactCandidate,
    ) -> AgentArtifactCandidate:
        async with self._session_factory() as session:
            run_repo = self._run_repo_factory(session)
            locked = await run_repo.get_by_id_for_update(
                request.turn_run_id, request.context_snapshot.owner_id
            )
            repo = self._agent_repo_factory(session)
            turn = await repo.get_turn_scoped(
                request.turn_run_id, request.context_snapshot.owner_id
            )
            agent_session = await repo.get_session_scoped(
                request.session_id, request.context_snapshot.owner_id
            )
            if (
                locked is None
                or locked.status is not RunStatus.RUNNING
                or locked.run_type != RunType.AGENT_TURN.value
                or turn is None
                or agent_session is None
                or turn.session_id != request.session_id
                or agent_session.project_id != request.context_snapshot.project_id
                or agent_session.active_turn_run_id != request.turn_run_id
                or source_scope.turn_run_id != request.turn_run_id
            ):
                raise AgentArtifactServiceError(
                    "artifact_scope_invalid", "Artifact 当前业务作用域已失效"
                )
            staged = AgentArtifactCandidate(
                candidate.candidate_id,
                candidate.owner_id,
                candidate.project_id,
                candidate.session_id,
                candidate.turn_run_id,
                candidate.name,
                candidate.media_type,
                candidate.content_ref,
                candidate.content_hash,
                candidate.size_bytes,
                AgentArtifactCandidateStatus.STAGED,
                candidate.created_at,
                source_url=candidate.source_url,
                source_url_hash=candidate.source_url_hash,
            )
            existing = await repo.get_or_add_candidate(staged)
            if not same_agent_artifact_candidate_fact(existing, staged):
                raise AgentArtifactServiceError(
                    "artifact_candidate_conflict", "Artifact Candidate 身份冲突"
                )
            if existing.status in {
                AgentArtifactCandidateStatus.VALIDATED,
                AgentArtifactCandidateStatus.COMMITTED,
            }:
                return existing
            if existing.status is AgentArtifactCandidateStatus.REJECTED:
                raise AgentArtifactServiceError(
                    "artifact_candidate_rejected", "Artifact Candidate 已被拒绝"
                )
            candidates = await repo.list_candidates_scoped(
                request.turn_run_id, request.context_snapshot.owner_id
            )
            accepted = tuple(
                value
                for value in candidates
                if value.status is not AgentArtifactCandidateStatus.REJECTED
            )
            if (
                len(accepted) > AGENT_ARTIFACT_MAX_PER_TURN
                or sum(value.size_bytes for value in accepted)
                > AGENT_ARTIFACT_MAX_TOTAL_BYTES_PER_TURN
            ):
                raise AgentArtifactServiceError(
                    "artifact_turn_budget_exceeded", "本轮 Artifact 数量或总量超过上限"
                )
            if not await repo.save_candidate(candidate, expected_status="staged"):
                current = await repo.get_candidate(candidate.candidate_id)
                if current is not None and current.status in {
                    AgentArtifactCandidateStatus.VALIDATED,
                    AgentArtifactCandidateStatus.COMMITTED,
                }:
                    return current
                raise RunConcurrentModificationError(request.turn_run_id)
            await self._event_repo_factory(session).add(
                create_event(
                    request.turn_run_id,
                    locked.event_sequence,
                    "agent_artifact_validated",
                    "system",
                    f"artifact:{candidate.candidate_id}",
                    {
                        "candidate_id": candidate.candidate_id,
                        "content_hash": candidate.content_hash,
                        "size_bytes": candidate.size_bytes,
                        "media_type": candidate.media_type,
                    },
                )
            )
            if not await run_repo.update_status(
                request.turn_run_id,
                RunStatus.RUNNING,
                RunStatus.RUNNING,
                locked.event_sequence + 1,
            ):
                raise RunConcurrentModificationError(request.turn_run_id)
            await session.commit()
        await notify_run_event(self._event_notifier, request.turn_run_id)
        return candidate

    async def _record_rejection(
        self,
        *,
        request: RuntimeTurnRequest,
        candidate_id: str,
        tool_call_id: str,
        code: str,
        name: str,
        media_type: str,
    ) -> None:
        digest = hashlib.sha256(
            json.dumps(
                {"tool_call_id": tool_call_id, "name": name, "media_type": media_type},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        staged = create_agent_artifact_candidate(
            candidate_id=candidate_id,
            owner_id=request.context_snapshot.owner_id,
            project_id=request.context_snapshot.project_id,
            session_id=request.session_id,
            turn_run_id=request.turn_run_id,
            name="rejected.txt",
            media_type="text/plain",
            content_ref="rejected://submission",
            content_hash=digest,
            size_bytes=0,
        )
        rejected = staged.reject(code)
        try:
            async with self._session_factory() as session:
                run_repo = self._run_repo_factory(session)
                locked = await run_repo.get_by_id_for_update(
                    request.turn_run_id, request.context_snapshot.owner_id
                )
                if locked is None or locked.status is not RunStatus.RUNNING:
                    return
                repo = self._agent_repo_factory(session)
                existing = await repo.get_or_add_candidate(staged)
                if existing.status is AgentArtifactCandidateStatus.STAGED:
                    if not await repo.save_candidate(rejected, expected_status="staged"):
                        return
                    await self._event_repo_factory(session).add(
                        create_event(
                            request.turn_run_id,
                            locked.event_sequence,
                            "agent_artifact_rejected",
                            "system",
                            f"artifact:{candidate_id}",
                            {"candidate_id": candidate_id, "error_code": code},
                        )
                    )
                    if not await run_repo.update_status(
                        request.turn_run_id,
                        RunStatus.RUNNING,
                        RunStatus.RUNNING,
                        locked.event_sequence + 1,
                    ):
                        return
                await session.commit()
        except Exception:
            return
        await notify_run_event(self._event_notifier, request.turn_run_id)


class AgentArtifactQueryService[TSession: Session]:
    """以 owner 闭包查询正式产物；每次下载重新校验 blob。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        storage: Storage,
    ) -> None:
        self._session_factory = session_factory
        self._agent_repo_factory = agent_repo_factory
        self._storage = storage

    async def list_artifacts(self, owner_id: str, run_id: str) -> tuple[AgentArtifact, ...]:
        async with self._session_factory() as session:
            repo = self._agent_repo_factory(session)
            if await repo.get_turn_scoped(run_id, owner_id) is None:
                raise AgentTurnNotFoundError(run_id)
            values = await repo.list_artifacts_scoped(run_id, owner_id)
            return tuple(values)

    async def content(self, owner_id: str, artifact_id: str) -> AgentArtifactContent:
        async with self._session_factory() as session:
            artifact = await self._agent_repo_factory(session).get_artifact_scoped(
                artifact_id, owner_id
            )
            if artifact is None:
                raise AgentArtifactNotFoundError(artifact_id)
        try:
            content = await self._storage.read(artifact.storage_key)
        except StorageError as exc:
            raise AgentArtifactServiceError(
                "artifact_content_unavailable",
                "Artifact 内容暂时不可用",
                temporary=True,
            ) from exc
        if (
            len(content) != artifact.size_bytes
            or hashlib.sha256(content).hexdigest() != artifact.content_hash
        ):
            raise AgentArtifactServiceError(
                "artifact_content_integrity_failed", "Artifact 内容完整性校验失败"
            )
        return AgentArtifactContent(artifact, content)
