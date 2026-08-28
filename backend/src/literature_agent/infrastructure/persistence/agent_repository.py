"""AgentSession/Turn 专用 PostgreSQL Repository。"""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.domain.agent_artifact import AgentArtifact
from literature_agent.domain.exceptions import RunConcurrentModificationError
from literature_agent.domain.mcp_configuration import McpPolicyRef, McpPolicyToolRef
from literature_agent.domain.research_agent import (
    AgentArtifactCandidate,
    AgentArtifactCandidateStatus,
    AgentMessage,
    AgentMessageRole,
    AgentSession,
    AgentSessionStatus,
    AgentTurnRun,
    ArtifactContextRef,
    AttachmentContextRef,
    ContextSnapshot,
    PolicySnapshot,
    ProjectIndexContextRef,
    RuntimeSessionBinding,
    RuntimeTurnBinding,
    same_agent_artifact_candidate_fact,
)
from literature_agent.domain.skill_configuration import SkillPolicyRef, SkillSource
from literature_agent.infrastructure.persistence.models import (
    AgentArtifactCandidateORM,
    AgentArtifactORM,
    AgentContextSnapshotORM,
    AgentMessageAttachmentORM,
    AgentMessageORM,
    AgentPolicySnapshotORM,
    AgentRuntimeSessionBindingORM,
    AgentRuntimeTurnBindingORM,
    AgentSandboxLeaseORM,
    AgentSessionORM,
    AgentTurnRunORM,
)


class SqlalchemyAgentRepository(AgentRepository):
    """以 scoped 读取和唯一约束保存 Agent 业务事实。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_session(self, value: AgentSession) -> AgentSession:
        self._session.add(
            AgentSessionORM(
                session_id=value.session_id,
                owner_id=value.owner_id,
                project_id=value.project_id,
                title=value.title,
                status=value.status.value,
                active_turn_run_id=value.active_turn_run_id,
                next_message_sequence=1,
                created_at=value.created_at,
                last_activity_at=value.last_activity_at,
            )
        )
        return value

    async def list_sessions_scoped(
        self, project_id: str, owner_id: str
    ) -> list[AgentSession]:
        rows = (
            (
                await self._session.execute(
                    select(AgentSessionORM)
                    .where(
                        AgentSessionORM.project_id == project_id,
                        AgentSessionORM.owner_id == owner_id,
                    )
                    .order_by(
                        AgentSessionORM.last_activity_at.desc(),
                        AgentSessionORM.session_id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_session(row) for row in rows]

    async def get_session_scoped(self, session_id: str, owner_id: str) -> AgentSession | None:
        row = (
            await self._session.execute(
                select(AgentSessionORM).where(
                    AgentSessionORM.session_id == session_id, AgentSessionORM.owner_id == owner_id
                )
            )
        ).scalar_one_or_none()
        return _session(row) if row else None

    async def get_session_scoped_for_update(
        self, session_id: str, owner_id: str
    ) -> AgentSession | None:
        row = (
            await self._session.execute(
                select(AgentSessionORM)
                .where(
                    AgentSessionORM.session_id == session_id, AgentSessionORM.owner_id == owner_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        return _session(row) if row else None

    async def try_claim_active_turn(self, session_id: str, turn_run_id: str) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentSessionORM)
                .where(
                    AgentSessionORM.session_id == session_id,
                    AgentSessionORM.active_turn_run_id.is_(None),
                )
                .values(
                    active_turn_run_id=turn_run_id,
                    last_activity_at=datetime.now(UTC),
                )
            ),
        )
        return result.rowcount == 1

    async def release_active_turn(self, session_id: str, turn_run_id: str) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentSessionORM)
                .where(
                    AgentSessionORM.session_id == session_id,
                    AgentSessionORM.active_turn_run_id == turn_run_id,
                )
                .values(
                    active_turn_run_id=None,
                    last_activity_at=datetime.now(UTC),
                )
            ),
        )
        return result.rowcount == 1

    async def allocate_message_sequence(self, session_id: str) -> int:
        row = (
            await self._session.execute(
                select(AgentSessionORM)
                .where(AgentSessionORM.session_id == session_id)
                .with_for_update()
            )
        ).scalar_one()
        sequence = row.next_message_sequence
        row.next_message_sequence += 1
        return sequence

    async def add_message(self, value: AgentMessage) -> AgentMessage:
        self._session.add(
            AgentMessageORM(
                message_id=value.message_id,
                session_id=value.session_id,
                sequence=value.sequence,
                role=value.role.value,
                content=value.content,
                turn_run_id=value.turn_run_id,
                idempotency_key=value.idempotency_key,
                created_at=value.created_at,
                claim_set_id=value.claim_set_id,
            )
        )
        if value.attachment_ids:
            # 未建 ORM relationship，因此先固化父行，再插入有序引用。
            await self._session.flush()
        for ordinal, attachment_id in enumerate(value.attachment_ids, start=1):
            self._session.add(
                AgentMessageAttachmentORM(
                    message_id=value.message_id,
                    attachment_id=attachment_id,
                    ordinal=ordinal,
                )
            )
        return value

    async def has_messages(self, session_id: str) -> bool:
        return bool(
            await self._session.scalar(
                select(exists().where(AgentMessageORM.session_id == session_id))
            )
        )

    async def list_messages_scoped(self, session_id: str, owner_id: str) -> list[AgentMessage]:
        rows = (
            (
                await self._session.execute(
                    select(AgentMessageORM)
                    .join(AgentSessionORM)
                    .where(
                        AgentMessageORM.session_id == session_id,
                        AgentSessionORM.owner_id == owner_id,
                    )
                    .order_by(AgentMessageORM.sequence)
                )
            )
            .scalars()
            .all()
        )
        attachment_ids = await self._message_attachment_ids([row.message_id for row in rows])
        return [_message(row, attachment_ids.get(row.message_id, ())) for row in rows]

    async def get_message_by_run_and_role(self, run_id: str, role: str) -> AgentMessage | None:
        row = (
            await self._session.execute(
                select(AgentMessageORM).where(
                    AgentMessageORM.turn_run_id == run_id, AgentMessageORM.role == role
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        attachment_ids = await self._message_attachment_ids([row.message_id])
        return _message(row, attachment_ids.get(row.message_id, ()))

    async def _message_attachment_ids(
        self, message_ids: list[str]
    ) -> dict[str, tuple[str, ...]]:
        if not message_ids:
            return {}
        rows = (
            await self._session.execute(
                select(AgentMessageAttachmentORM).where(
                    AgentMessageAttachmentORM.message_id.in_(message_ids)
                ).order_by(
                    AgentMessageAttachmentORM.message_id,
                    AgentMessageAttachmentORM.ordinal,
                )
            )
        ).scalars().all()
        values: dict[str, list[str]] = {}
        for row in rows:
            values.setdefault(row.message_id, []).append(row.attachment_id)
        return {key: tuple(value) for key, value in values.items()}

    async def add_turn(self, value: AgentTurnRun) -> AgentTurnRun:
        self._session.add(
            AgentTurnRunORM(
                turn_run_id=value.turn_run_id,
                session_id=value.session_id,
                user_message_id=value.user_message_id,
                context_snapshot_id=value.context_snapshot_id,
                policy_snapshot_id=value.policy_snapshot_id,
            )
        )
        return value

    async def get_turn_scoped(self, run_id: str, owner_id: str) -> AgentTurnRun | None:
        row = (
            await self._session.execute(
                select(AgentTurnRunORM)
                .join(
                    AgentSessionORM,
                    AgentTurnRunORM.session_id == AgentSessionORM.session_id,
                )
                .where(AgentTurnRunORM.turn_run_id == run_id, AgentSessionORM.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        return _turn(row) if row else None

    async def add_context_snapshot(self, value: ContextSnapshot) -> ContextSnapshot:
        self._session.add(
            AgentContextSnapshotORM(
                snapshot_id=value.snapshot_id,
                schema_version=value.schema_version,
                owner_id=value.owner_id,
                project_id=value.project_id,
                session_id=value.session_id,
                turn_run_id=value.turn_run_id,
                user_message_id=value.user_message_id,
                history_through_sequence=value.history_through_sequence,
                project_index_refs=[
                    {
                        "paper_id": r.paper_id,
                        "paper_version_id": r.paper_version_id,
                        "chunk_set_id": r.chunk_set_id,
                    }
                    for r in value.project_index_refs
                ],
                review_output_id=value.review_output_id,
                artifact_refs=[
                    {"artifact_id": r.artifact_id, "content_hash": r.content_hash}
                    for r in value.artifact_refs
                ],
                attachment_refs=[
                    {
                        "attachment_id": r.attachment_id,
                        "version": r.version,
                        "content_hash": r.content_hash,
                        "size_bytes": r.size_bytes,
                        "media_type": r.media_type,
                        "display_name": r.display_name,
                    }
                    for r in value.attachment_refs
                ],
                snapshot_hash=value.snapshot_hash,
                created_at=value.created_at,
            )
        )
        return value

    async def get_context_snapshot(self, snapshot_id: str) -> ContextSnapshot | None:
        row = await self._session.get(AgentContextSnapshotORM, snapshot_id)
        return _context(row) if row else None

    async def add_policy_snapshot(self, value: PolicySnapshot) -> PolicySnapshot:
        self._session.add(
            AgentPolicySnapshotORM(
                snapshot_id=value.snapshot_id,
                policy_version=value.policy_version,
                owner_id=value.owner_id,
                project_id=value.project_id,
                session_id=value.session_id,
                turn_run_id=value.turn_run_id,
                allowed_tool_names=list(value.allowed_tool_names),
                allowed_skill_names=list(value.allowed_skill_names),
                skill_refs=[
                    {
                        "profile_id": ref.profile_id,
                        "profile_revision": ref.profile_revision,
                        "skill_id": ref.skill_id,
                        "source": ref.source.value,
                        "version": ref.version,
                        "name": ref.name,
                        "content_hash": ref.content_hash,
                        "required_tool_names": list(ref.required_tool_names),
                    }
                    for ref in value.skill_refs
                ],
                mcp_refs=[
                    {
                        "profile_id": ref.profile_id,
                        "profile_revision": ref.profile_revision,
                        "catalog_id": ref.catalog_id,
                        "version": ref.version,
                        "config_hash": ref.config_hash,
                        "tools": [
                            {
                                "name": tool.name,
                                "input_schema_hash": tool.input_schema_hash,
                            }
                            for tool in ref.tools
                        ],
                    }
                    for ref in value.mcp_refs
                ],
                network_enabled=value.network_enabled,
                sandbox_enabled=value.sandbox_enabled,
                approval_required=value.approval_required,
                max_model_calls=value.max_model_calls,
                max_tool_calls=value.max_tool_calls,
                snapshot_hash=value.snapshot_hash,
                created_at=value.created_at,
            )
        )
        return value

    async def get_policy_snapshot(self, snapshot_id: str) -> PolicySnapshot | None:
        row = await self._session.get(AgentPolicySnapshotORM, snapshot_id)
        return _policy(row) if row else None

    async def get_or_add_session_binding(
        self, value: RuntimeSessionBinding
    ) -> RuntimeSessionBinding:
        await self._session.execute(
            insert(AgentRuntimeSessionBindingORM)
            .values(
                binding_id=value.binding_id,
                session_id=value.session_id,
                generation=value.generation,
                runtime_thread_id=value.runtime_thread_id,
                runtime_workspace_id=value.runtime_workspace_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    AgentRuntimeSessionBindingORM.session_id,
                    AgentRuntimeSessionBindingORM.generation,
                ]
            )
        )
        found = await self.get_session_binding_generation(value.session_id, value.generation)
        assert found is not None
        return found

    async def get_or_add_turn_binding(self, value: RuntimeTurnBinding) -> RuntimeTurnBinding:
        await self._session.execute(
            insert(AgentRuntimeTurnBindingORM)
            .values(
                turn_run_id=value.turn_run_id,
                session_id=value.session_id,
                session_binding_id=value.session_binding_id,
                runtime_execution_id=value.runtime_execution_id,
                runtime_checkpoint_id=value.runtime_checkpoint_id,
            )
            .on_conflict_do_nothing(index_elements=[AgentRuntimeTurnBindingORM.turn_run_id])
        )
        found = await self.get_turn_binding(value.turn_run_id)
        assert found is not None
        return found

    async def get_session_binding(self, session_id: str) -> RuntimeSessionBinding | None:
        row = (
            await self._session.execute(
                select(AgentRuntimeSessionBindingORM)
                .where(AgentRuntimeSessionBindingORM.session_id == session_id)
                .order_by(AgentRuntimeSessionBindingORM.generation.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _session_binding(row) if row else None

    async def get_session_binding_generation(
        self, session_id: str, generation: int
    ) -> RuntimeSessionBinding | None:
        row = (
            await self._session.execute(
                select(AgentRuntimeSessionBindingORM).where(
                    AgentRuntimeSessionBindingORM.session_id == session_id,
                    AgentRuntimeSessionBindingORM.generation == generation,
                )
            )
        ).scalar_one_or_none()
        return _session_binding(row) if row else None

    async def get_turn_binding(self, run_id: str) -> RuntimeTurnBinding | None:
        row = await self._session.get(AgentRuntimeTurnBindingORM, run_id)
        return (
            RuntimeTurnBinding(
                session_id=row.session_id,
                turn_run_id=row.turn_run_id,
                session_binding_id=row.session_binding_id,
                runtime_execution_id=row.runtime_execution_id,
                runtime_checkpoint_id=row.runtime_checkpoint_id,
            )
            if row
            else None
        )

    async def get_or_add_candidate(self, value: AgentArtifactCandidate) -> AgentArtifactCandidate:
        await self._session.execute(
            insert(AgentArtifactCandidateORM)
            .values(
                candidate_id=value.candidate_id,
                owner_id=value.owner_id,
                project_id=value.project_id,
                session_id=value.session_id,
                turn_run_id=value.turn_run_id,
                name=value.name,
                media_type=value.media_type,
                content_ref=value.content_ref,
                content_hash=value.content_hash,
                size_bytes=value.size_bytes,
                status=value.status.value,
                tool_call_id=value.tool_call_id,
                storage_key=value.storage_key,
                sandbox_generation=value.sandbox_generation,
                sandbox_fencing_token=value.sandbox_fencing_token,
                rejection_code=value.rejection_code,
                validated_at=value.validated_at,
                committed_at=value.committed_at,
                created_at=value.created_at,
            )
            .on_conflict_do_nothing()
        )
        by_id = await self._session.get(AgentArtifactCandidateORM, value.candidate_id)
        if by_id is not None:
            found = _candidate(by_id)
            if same_agent_artifact_candidate_fact(found, value):
                return found
            raise RunConcurrentModificationError(value.turn_run_id)
        by_turn_hash = (
            await self._session.execute(
                select(AgentArtifactCandidateORM).where(
                    AgentArtifactCandidateORM.turn_run_id == value.turn_run_id,
                    AgentArtifactCandidateORM.content_hash == value.content_hash,
                )
            )
        ).scalar_one_or_none()
        if by_turn_hash is not None:
            found = _candidate(by_turn_hash)
            if same_agent_artifact_candidate_fact(found, value):
                return found
            raise RunConcurrentModificationError(value.turn_run_id)
        raise RunConcurrentModificationError(value.turn_run_id)

    async def list_candidates_scoped(
        self, run_id: str, owner_id: str
    ) -> list[AgentArtifactCandidate]:
        rows = (
            (
                await self._session.execute(
                    select(AgentArtifactCandidateORM)
                    .where(
                        AgentArtifactCandidateORM.turn_run_id == run_id,
                        AgentArtifactCandidateORM.owner_id == owner_id,
                    )
                    .order_by(AgentArtifactCandidateORM.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [_candidate(row) for row in rows]

    async def get_candidate(self, candidate_id: str) -> AgentArtifactCandidate | None:
        row = await self._session.get(AgentArtifactCandidateORM, candidate_id)
        return _candidate(row) if row is not None else None

    async def is_sandbox_fence_current(
        self,
        *,
        owner_id: str,
        project_id: str,
        session_id: str,
        turn_run_id: str,
        sandbox_generation: int,
        sandbox_fencing_token: int,
    ) -> bool:
        """在正式发布事务中复核 Candidate 仍属于当前 ACTIVE Sandbox fence。"""
        value = await self._session.scalar(
            select(AgentSandboxLeaseORM.session_id)
            .where(
                AgentSandboxLeaseORM.owner_id == owner_id,
                AgentSandboxLeaseORM.project_id == project_id,
                AgentSandboxLeaseORM.session_id == session_id,
                AgentSandboxLeaseORM.holder_turn_run_id == turn_run_id,
                AgentSandboxLeaseORM.generation == sandbox_generation,
                AgentSandboxLeaseORM.fencing_token == sandbox_fencing_token,
                AgentSandboxLeaseORM.status == "active",
            )
            .with_for_update()
        )
        return value is not None

    async def save_candidate(
        self,
        value: AgentArtifactCandidate,
        *,
        expected_status: str,
    ) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentArtifactCandidateORM)
                .where(
                    AgentArtifactCandidateORM.candidate_id == value.candidate_id,
                    AgentArtifactCandidateORM.status == expected_status,
                )
                .values(
                    status=value.status.value,
                    tool_call_id=value.tool_call_id,
                    storage_key=value.storage_key,
                    sandbox_generation=value.sandbox_generation,
                    sandbox_fencing_token=value.sandbox_fencing_token,
                    rejection_code=value.rejection_code,
                    validated_at=value.validated_at,
                    committed_at=value.committed_at,
                )
            ),
        )
        return result.rowcount == 1

    async def add_artifact_if_absent(self, value: AgentArtifact) -> AgentArtifact:
        await self._session.execute(
            insert(AgentArtifactORM)
            .values(
                artifact_id=value.artifact_id,
                candidate_id=value.candidate_id,
                owner_id=value.owner_id,
                project_id=value.project_id,
                session_id=value.session_id,
                turn_run_id=value.turn_run_id,
                name=value.name,
                media_type=value.media_type,
                content_hash=value.content_hash,
                size_bytes=value.size_bytes,
                storage_key=value.storage_key,
                created_at=value.created_at,
            )
            .on_conflict_do_nothing(index_elements=[AgentArtifactORM.candidate_id])
        )
        row = (
            await self._session.execute(
                select(AgentArtifactORM).where(AgentArtifactORM.candidate_id == value.candidate_id)
            )
        ).scalar_one()
        found = _artifact(row)
        if found != value and (
            found.artifact_id,
            found.candidate_id,
            found.owner_id,
            found.project_id,
            found.session_id,
            found.turn_run_id,
            found.name,
            found.media_type,
            found.content_hash,
            found.size_bytes,
            found.storage_key,
        ) != (
            value.artifact_id,
            value.candidate_id,
            value.owner_id,
            value.project_id,
            value.session_id,
            value.turn_run_id,
            value.name,
            value.media_type,
            value.content_hash,
            value.size_bytes,
            value.storage_key,
        ):
            raise RunConcurrentModificationError(value.turn_run_id)
        return found

    async def list_artifacts_scoped(self, run_id: str, owner_id: str) -> list[AgentArtifact]:
        rows = (
            (
                await self._session.execute(
                    select(AgentArtifactORM)
                    .join(
                        AgentTurnRunORM,
                        AgentArtifactORM.turn_run_id == AgentTurnRunORM.turn_run_id,
                    )
                    .join(
                        AgentSessionORM,
                        AgentTurnRunORM.session_id == AgentSessionORM.session_id,
                    )
                    .where(
                        AgentArtifactORM.turn_run_id == run_id,
                        AgentArtifactORM.owner_id == owner_id,
                        AgentSessionORM.owner_id == owner_id,
                        AgentArtifactORM.project_id == AgentSessionORM.project_id,
                        AgentArtifactORM.session_id == AgentSessionORM.session_id,
                    )
                    .order_by(AgentArtifactORM.created_at, AgentArtifactORM.artifact_id)
                )
            )
            .scalars()
            .all()
        )
        return [_artifact(row) for row in rows]

    async def get_artifact_scoped(self, artifact_id: str, owner_id: str) -> AgentArtifact | None:
        row = (
            await self._session.execute(
                select(AgentArtifactORM)
                .join(
                    AgentTurnRunORM,
                    AgentArtifactORM.turn_run_id == AgentTurnRunORM.turn_run_id,
                )
                .join(
                    AgentSessionORM,
                    AgentTurnRunORM.session_id == AgentSessionORM.session_id,
                )
                .where(
                    AgentArtifactORM.artifact_id == artifact_id,
                    AgentArtifactORM.owner_id == owner_id,
                    AgentSessionORM.owner_id == owner_id,
                    AgentArtifactORM.project_id == AgentSessionORM.project_id,
                    AgentArtifactORM.session_id == AgentSessionORM.session_id,
                )
            )
        ).scalar_one_or_none()
        return _artifact(row) if row is not None else None


def _session(row: AgentSessionORM) -> AgentSession:
    return AgentSession(
        row.session_id,
        row.owner_id,
        row.project_id,
        row.title,
        AgentSessionStatus(row.status),
        row.active_turn_run_id,
        row.created_at,
        row.last_activity_at,
    )


def _message(
    row: AgentMessageORM, attachment_ids: tuple[str, ...] = ()
) -> AgentMessage:
    return AgentMessage(
        row.message_id,
        row.session_id,
        row.sequence,
        AgentMessageRole(row.role),
        row.content,
        row.turn_run_id,
        row.idempotency_key,
        row.created_at,
        row.claim_set_id,
        attachment_ids,
    )


def _turn(row: AgentTurnRunORM) -> AgentTurnRun:
    return AgentTurnRun(
        row.turn_run_id,
        row.session_id,
        row.user_message_id,
        row.context_snapshot_id,
        row.policy_snapshot_id,
    )


def _context(row: AgentContextSnapshotORM) -> ContextSnapshot:
    return ContextSnapshot(
        snapshot_id=row.snapshot_id,
        schema_version=row.schema_version,
        owner_id=row.owner_id,
        project_id=row.project_id,
        session_id=row.session_id,
        turn_run_id=row.turn_run_id,
        user_message_id=row.user_message_id,
        history_through_sequence=row.history_through_sequence,
        project_index_refs=tuple(ProjectIndexContextRef(**r) for r in row.project_index_refs),
        review_output_id=row.review_output_id,
        artifact_refs=tuple(ArtifactContextRef(**r) for r in row.artifact_refs),
        snapshot_hash=row.snapshot_hash,
        created_at=row.created_at,
        attachment_refs=tuple(
            AttachmentContextRef(**r) for r in row.attachment_refs
        ),
    )


def _policy(row: AgentPolicySnapshotORM) -> PolicySnapshot:
    return PolicySnapshot(
        row.snapshot_id,
        row.policy_version,
        row.owner_id,
        row.project_id,
        row.session_id,
        row.turn_run_id,
        tuple(row.allowed_tool_names),
        tuple(row.allowed_skill_names),
        tuple(
            SkillPolicyRef(
                profile_id=ref["profile_id"],
                profile_revision=ref["profile_revision"],
                skill_id=ref["skill_id"],
                source=SkillSource(ref["source"]),
                version=ref["version"],
                name=ref["name"],
                content_hash=ref["content_hash"],
                required_tool_names=tuple(ref["required_tool_names"]),
            )
            for ref in row.skill_refs
        ),
        tuple(
            McpPolicyRef(
                profile_id=ref["profile_id"],
                profile_revision=ref["profile_revision"],
                catalog_id=ref["catalog_id"],
                version=ref["version"],
                config_hash=ref["config_hash"],
                tools=tuple(McpPolicyToolRef(**tool) for tool in ref["tools"]),
            )
            for ref in row.mcp_refs
        ),
        row.network_enabled,
        row.sandbox_enabled,
        row.approval_required,
        row.max_model_calls,
        row.max_tool_calls,
        row.snapshot_hash,
        row.created_at,
    )


def _session_binding(row: AgentRuntimeSessionBindingORM) -> RuntimeSessionBinding:
    return RuntimeSessionBinding(
        session_id=row.session_id,
        binding_id=row.binding_id,
        generation=row.generation,
        runtime_thread_id=row.runtime_thread_id,
        runtime_workspace_id=row.runtime_workspace_id,
    )


def _candidate(row: AgentArtifactCandidateORM) -> AgentArtifactCandidate:
    return AgentArtifactCandidate(
        row.candidate_id,
        row.owner_id,
        row.project_id,
        row.session_id,
        row.turn_run_id,
        row.name,
        row.media_type,
        row.content_ref,
        row.content_hash,
        row.size_bytes,
        AgentArtifactCandidateStatus(row.status),
        row.created_at,
        row.tool_call_id,
        row.storage_key,
        row.sandbox_generation,
        row.sandbox_fencing_token,
        row.rejection_code,
        row.validated_at,
        row.committed_at,
    )


def _artifact(row: AgentArtifactORM) -> AgentArtifact:
    return AgentArtifact(
        row.artifact_id,
        row.candidate_id,
        row.owner_id,
        row.project_id,
        row.session_id,
        row.turn_run_id,
        row.name,
        row.media_type,
        row.content_hash,
        row.size_bytes,
        row.storage_key,
        row.created_at,
    )
