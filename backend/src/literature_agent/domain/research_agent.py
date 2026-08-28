"""Research Agent 持续会话与逐轮执行的领域契约。"""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from literature_agent.domain.mcp_configuration import McpPolicyRef
from literature_agent.domain.skill_configuration import SkillPolicyRef

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_AGENT_TITLE_MAX_LENGTH = 200
_AGENT_MESSAGE_MAX_LENGTH = 16_000
_AGENT_CANDIDATE_ID_MAX_LENGTH = 255
_AGENT_CANDIDATE_NAME_MAX_LENGTH = 255
_AGENT_CANDIDATE_MEDIA_TYPE_MAX_LENGTH = 255
_AGENT_CANDIDATE_CONTENT_REF_MAX_LENGTH = 500
_AGENT_CANDIDATE_MAX_SIZE_BYTES = 10 * 1024 * 1024
AGENT_MESSAGE_MAX_ATTACHMENTS = 5

PROJECT_RESEARCH_WORKSPACE_POLICY_VERSION = "agent-policy.project-research-workspace.v2"
PROJECT_RESEARCH_WORKSPACE_MCP_POLICY_VERSION = (
    "agent-policy.project-research-workspace-mcp.v2"
)
PROJECT_RESEARCH_CAPABILITIES_POLICY_VERSION = (
    "agent-policy.project-research-capabilities.v2"
)
PROJECT_RESEARCH_WORKSPACE_TOOLS = (
    "search_project_chunks",
    "read_review_evidence_matrix",
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
    "submit_artifact",
)


class AgentSessionStatus(StrEnum):
    """AgentSession 生命周期；Session 本身不是后台 Run。"""

    ACTIVE = "active"
    CLOSED = "closed"


class AgentMessageRole(StrEnum):
    """用户可见 Agent 消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"


class AgentArtifactCandidateStatus(StrEnum):
    """候选产物必须经过显式校验并随 Turn 成功提交。"""

    STAGED = "staged"
    VALIDATED = "validated"
    COMMITTED = "committed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AgentSession:
    """绑定 owner/Project 的持续研究会话。"""

    session_id: str
    owner_id: str
    project_id: str
    title: str | None
    status: AgentSessionStatus
    active_turn_run_id: str | None
    created_at: datetime
    last_activity_at: datetime


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """AgentSession 内按 sequence 排序的一条用户可见消息。"""

    message_id: str
    session_id: str
    sequence: int
    role: AgentMessageRole
    content: str
    turn_run_id: str
    idempotency_key: str
    created_at: datetime
    claim_set_id: str | None = None
    attachment_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_attachment_ids(self.attachment_ids)
        if self.role is AgentMessageRole.ASSISTANT and self.attachment_ids:
            raise ValueError("Assistant Message 不能引用用户输入附件")


@dataclass(frozen=True, slots=True)
class AgentTurnRun:
    """通用 ``agent_turn`` Run 的最小关联记录。"""

    turn_run_id: str
    session_id: str
    user_message_id: str
    context_snapshot_id: str
    policy_snapshot_id: str


@dataclass(frozen=True, slots=True)
class AgentArtifactCandidate:
    """尚未成为正式 Artifact 的小型候选元数据。"""

    candidate_id: str
    owner_id: str
    project_id: str
    session_id: str
    turn_run_id: str
    name: str
    media_type: str
    content_ref: str
    content_hash: str
    size_bytes: int
    status: AgentArtifactCandidateStatus
    created_at: datetime
    tool_call_id: str | None = None
    storage_key: str | None = None
    sandbox_generation: int | None = None
    sandbox_fencing_token: int | None = None
    rejection_code: str | None = None
    validated_at: datetime | None = None
    committed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty(
            candidate_id=self.candidate_id,
            owner_id=self.owner_id,
            project_id=self.project_id,
            session_id=self.session_id,
            turn_run_id=self.turn_run_id,
            name=self.name,
            media_type=self.media_type,
            content_ref=self.content_ref,
        )
        _require_max_length(
            candidate_id=(self.candidate_id, _AGENT_CANDIDATE_ID_MAX_LENGTH),
            name=(self.name, _AGENT_CANDIDATE_NAME_MAX_LENGTH),
            media_type=(self.media_type, _AGENT_CANDIDATE_MEDIA_TYPE_MAX_LENGTH),
            content_ref=(self.content_ref, _AGENT_CANDIDATE_CONTENT_REF_MAX_LENGTH),
        )
        if not _SHA256_PATTERN.fullmatch(self.content_hash):
            raise ValueError("AgentArtifactCandidate content_hash 必须是小写 SHA-256")
        if not 0 <= self.size_bytes <= _AGENT_CANDIDATE_MAX_SIZE_BYTES:
            raise ValueError("AgentArtifactCandidate size_bytes 必须在 0..10_MiB 范围内")
        if self.status is AgentArtifactCandidateStatus.STAGED:
            if any(
                value is not None
                for value in (
                    self.tool_call_id,
                    self.storage_key,
                    self.sandbox_generation,
                    self.sandbox_fencing_token,
                    self.rejection_code,
                    self.validated_at,
                    self.committed_at,
                )
            ):
                raise ValueError("STAGED Candidate 不能携带已校验或终态字段")
        elif self.status is AgentArtifactCandidateStatus.REJECTED:
            if (
                not self.rejection_code
                or self.tool_call_id is not None
                or self.storage_key is not None
                or self.sandbox_generation is not None
                or self.sandbox_fencing_token is not None
                or self.validated_at is not None
                or self.committed_at is not None
            ):
                raise ValueError("REJECTED Candidate 必须只携带安全拒绝码")
        else:
            if (
                not self.tool_call_id
                or not self.storage_key
                or self.sandbox_generation is None
                or self.sandbox_generation < 1
                or self.sandbox_fencing_token is None
                or self.sandbox_fencing_token < 1
                or self.validated_at is None
                or self.rejection_code is not None
            ):
                raise ValueError("VALIDATED/COMMITTED Candidate 缺少校验与 fence 事实")
            if self.status is AgentArtifactCandidateStatus.COMMITTED and self.committed_at is None:
                raise ValueError("COMMITTED Candidate 缺少 committed_at")
            if (
                self.status is AgentArtifactCandidateStatus.VALIDATED
                and self.committed_at is not None
            ):
                raise ValueError("VALIDATED Candidate 不能提前携带 committed_at")

    def validate(
        self,
        *,
        tool_call_id: str,
        storage_key: str,
        sandbox_generation: int,
        sandbox_fencing_token: int,
        now: datetime | None = None,
    ) -> "AgentArtifactCandidate":
        """只允许 STAGED 幂等推进到 VALIDATED。"""
        if self.status is AgentArtifactCandidateStatus.VALIDATED:
            return self
        if self.status is not AgentArtifactCandidateStatus.STAGED:
            raise ValueError("Candidate 当前状态不能校验")
        return replace(
            self,
            status=AgentArtifactCandidateStatus.VALIDATED,
            tool_call_id=tool_call_id,
            storage_key=storage_key,
            sandbox_generation=sandbox_generation,
            sandbox_fencing_token=sandbox_fencing_token,
            validated_at=now or datetime.now(UTC),
        )

    def commit(self, *, now: datetime | None = None) -> "AgentArtifactCandidate":
        """只有已校验 Candidate 能随业务 Turn 成功提交。"""
        if self.status is AgentArtifactCandidateStatus.COMMITTED:
            return self
        if self.status is not AgentArtifactCandidateStatus.VALIDATED:
            raise ValueError("只有 VALIDATED Candidate 可以提交")
        return replace(
            self,
            status=AgentArtifactCandidateStatus.COMMITTED,
            committed_at=now or datetime.now(UTC),
        )

    def reject(self, code: str) -> "AgentArtifactCandidate":
        """永久非法 Candidate 进入不可恢复拒绝终态。"""
        if self.status is AgentArtifactCandidateStatus.REJECTED:
            return self
        if self.status is not AgentArtifactCandidateStatus.STAGED:
            raise ValueError("只有 STAGED Candidate 可以拒绝")
        if not code.strip() or len(code) > 100:
            raise ValueError("Candidate rejection_code 非法")
        return replace(
            self,
            status=AgentArtifactCandidateStatus.REJECTED,
            rejection_code=code,
        )


@dataclass(frozen=True, slots=True)
class RuntimeSessionBinding:
    """Session 某一代稳定 Binding 到 Runtime Thread/Workspace 的映射。"""

    session_id: str
    binding_id: str
    generation: int
    runtime_thread_id: str
    runtime_workspace_id: str

    def __post_init__(self) -> None:
        _require_non_empty(
            session_id=self.session_id,
            binding_id=self.binding_id,
            runtime_thread_id=self.runtime_thread_id,
            runtime_workspace_id=self.runtime_workspace_id,
        )
        if self.generation < 1:
            raise ValueError("RuntimeSessionBinding generation 必须是正整数")


@dataclass(frozen=True, slots=True)
class RuntimeTurnBinding:
    """Turn Run 到具体 Session Binding 及 Runtime Execution/Checkpoint 的映射。"""

    session_id: str
    turn_run_id: str
    session_binding_id: str
    runtime_execution_id: str
    runtime_checkpoint_id: str

    def __post_init__(self) -> None:
        _require_non_empty(
            session_id=self.session_id,
            turn_run_id=self.turn_run_id,
            session_binding_id=self.session_binding_id,
            runtime_execution_id=self.runtime_execution_id,
            runtime_checkpoint_id=self.runtime_checkpoint_id,
        )


@dataclass(frozen=True, slots=True)
class ProjectIndexContextRef:
    """一次 Turn 获准读取的 PaperVersion/ChunkSet 版本引用。"""

    paper_id: str
    paper_version_id: str
    chunk_set_id: str

    def __post_init__(self) -> None:
        _require_non_empty(
            paper_id=self.paper_id,
            paper_version_id=self.paper_version_id,
            chunk_set_id=self.chunk_set_id,
        )


@dataclass(frozen=True, slots=True)
class ArtifactContextRef:
    """一次 Turn 获准读取的 Artifact 及其内容版本。"""

    artifact_id: str
    content_hash: str

    def __post_init__(self) -> None:
        _require_non_empty(artifact_id=self.artifact_id)
        if not _SHA256_PATTERN.fullmatch(self.content_hash):
            raise ValueError("Artifact content_hash 必须是小写 SHA-256")


@dataclass(frozen=True, slots=True)
class AttachmentContextRef:
    """当前 Turn 明确授权并冻结版本的用户输入附件。"""

    attachment_id: str
    version: int
    content_hash: str
    size_bytes: int
    media_type: str
    display_name: str

    def __post_init__(self) -> None:
        _require_non_empty(
            attachment_id=self.attachment_id,
            media_type=self.media_type,
            display_name=self.display_name,
        )
        if self.version != 1:
            raise ValueError("Attachment Context 首版只接受 version=1")
        if not _SHA256_PATTERN.fullmatch(self.content_hash):
            raise ValueError("Attachment Context content_hash 必须是小写 SHA-256")
        if not 0 <= self.size_bytes <= _AGENT_CANDIDATE_MAX_SIZE_BYTES:
            raise ValueError("Attachment Context size_bytes 必须在 0..10_MiB 范围内")


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Turn 创建时固化的小型授权上下文引用。"""

    snapshot_id: str
    schema_version: str
    owner_id: str
    project_id: str
    session_id: str
    turn_run_id: str
    user_message_id: str
    history_through_sequence: int
    project_index_refs: tuple[ProjectIndexContextRef, ...]
    review_output_id: str | None
    artifact_refs: tuple[ArtifactContextRef, ...]
    snapshot_hash: str
    created_at: datetime
    attachment_refs: tuple[AttachmentContextRef, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """Turn 创建时固化的能力开关、审批要求和调用预算。"""

    snapshot_id: str
    policy_version: str
    owner_id: str
    project_id: str
    session_id: str
    turn_run_id: str
    allowed_tool_names: tuple[str, ...]
    allowed_skill_names: tuple[str, ...]
    skill_refs: tuple[SkillPolicyRef, ...]
    mcp_refs: tuple[McpPolicyRef, ...]
    network_enabled: bool
    sandbox_enabled: bool
    approval_required: bool
    max_model_calls: int
    max_tool_calls: int
    snapshot_hash: str
    created_at: datetime


def create_agent_session(*, owner_id: str, project_id: str, title: str | None) -> AgentSession:
    """创建绑定后不可换 owner/Project 的 AgentSession。"""
    _require_non_empty(owner_id=owner_id, project_id=project_id)
    normalized_title = title.strip() if title is not None else None
    normalized_title = normalized_title or None
    if normalized_title is not None and len(normalized_title) > _AGENT_TITLE_MAX_LENGTH:
        raise ValueError(f"AgentSession 标题长度不能超过 {_AGENT_TITLE_MAX_LENGTH}")
    now = datetime.now(UTC)
    return AgentSession(
        session_id=str(uuid4()),
        owner_id=owner_id,
        project_id=project_id,
        title=normalized_title,
        status=AgentSessionStatus.ACTIVE,
        active_turn_run_id=None,
        created_at=now,
        last_activity_at=now,
    )


def claim_active_turn(session: AgentSession, turn_run_id: str) -> AgentSession:
    """幂等认领 Session 的唯一活动 Turn。"""
    _require_non_empty(turn_run_id=turn_run_id)
    if session.status is not AgentSessionStatus.ACTIVE:
        raise ValueError("已关闭的 AgentSession 不能开始 Turn")
    if session.active_turn_run_id == turn_run_id:
        return session
    if session.active_turn_run_id is not None:
        raise ValueError("AgentSession 已有活动 Turn")
    return replace(
        session,
        active_turn_run_id=turn_run_id,
        last_activity_at=datetime.now(UTC),
    )


def release_active_turn(session: AgentSession, turn_run_id: str) -> AgentSession:
    """仅由当前活动 Turn 幂等释放 Session。"""
    if session.active_turn_run_id is None:
        return session
    if session.active_turn_run_id != turn_run_id:
        raise ValueError("待释放 Turn 与 AgentSession 当前活动 Turn 不匹配")
    return replace(
        session,
        active_turn_run_id=None,
        last_activity_at=datetime.now(UTC),
    )


def create_agent_message(
    *,
    session_id: str,
    last_sequence: int,
    role: AgentMessageRole,
    content: str,
    turn_run_id: str,
    idempotency_key: str,
    claim_set_id: str | None = None,
    attachment_ids: tuple[str, ...] = (),
) -> AgentMessage:
    """创建严格占用 Session 下一 sequence 的消息。"""
    _require_non_empty(
        session_id=session_id,
        turn_run_id=turn_run_id,
        idempotency_key=idempotency_key,
    )
    if last_sequence < 0:
        raise ValueError("Session last_sequence 不能小于 0")
    if not content.strip():
        raise ValueError("AgentMessage 内容不能为空")
    if len(content) > _AGENT_MESSAGE_MAX_LENGTH:
        raise ValueError(f"AgentMessage 内容长度不能超过 {_AGENT_MESSAGE_MAX_LENGTH}")
    _validate_attachment_ids(attachment_ids)
    if role is AgentMessageRole.ASSISTANT and attachment_ids:
        raise ValueError("Assistant Message 不能引用用户输入附件")
    return AgentMessage(
        message_id=str(uuid4()),
        session_id=session_id,
        sequence=last_sequence + 1,
        role=role,
        content=content,
        turn_run_id=turn_run_id,
        idempotency_key=idempotency_key,
        created_at=datetime.now(UTC),
        claim_set_id=claim_set_id,
        attachment_ids=attachment_ids,
    )


def create_agent_turn_run(
    *,
    turn_run_id: str,
    session_id: str,
    user_message_id: str,
    context_snapshot_id: str,
    policy_snapshot_id: str,
) -> AgentTurnRun:
    """创建一条通用 Run 到 Agent Turn 所有权记录的关联。"""
    _require_non_empty(
        turn_run_id=turn_run_id,
        session_id=session_id,
        user_message_id=user_message_id,
        context_snapshot_id=context_snapshot_id,
        policy_snapshot_id=policy_snapshot_id,
    )
    return AgentTurnRun(
        turn_run_id=turn_run_id,
        session_id=session_id,
        user_message_id=user_message_id,
        context_snapshot_id=context_snapshot_id,
        policy_snapshot_id=policy_snapshot_id,
    )


def create_agent_artifact_candidate(
    *,
    candidate_id: str,
    owner_id: str,
    project_id: str,
    session_id: str,
    turn_run_id: str,
    name: str,
    media_type: str,
    content_ref: str,
    content_hash: str,
    size_bytes: int,
) -> AgentArtifactCandidate:
    """校验不可信 Runtime descriptor 并创建 staged 候选业务事实。"""
    return AgentArtifactCandidate(
        candidate_id=candidate_id,
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        name=name,
        media_type=media_type,
        content_ref=content_ref,
        content_hash=content_hash,
        size_bytes=size_bytes,
        status=AgentArtifactCandidateStatus.STAGED,
        created_at=datetime.now(UTC),
    )


def same_agent_artifact_candidate_fact(
    left: AgentArtifactCandidate,
    right: AgentArtifactCandidate,
) -> bool:
    """比较候选内容身份；忽略生命周期、物理位置与创建时间。"""
    return (
        left.owner_id,
        left.project_id,
        left.session_id,
        left.turn_run_id,
        left.name,
        left.media_type,
        left.content_ref,
        left.content_hash,
        left.size_bytes,
    ) == (
        right.owner_id,
        right.project_id,
        right.session_id,
        right.turn_run_id,
        right.name,
        right.media_type,
        right.content_ref,
        right.content_hash,
        right.size_bytes,
    )


def create_context_snapshot(
    *,
    owner_id: str,
    project_id: str,
    session_id: str,
    turn_run_id: str,
    user_message_id: str,
    history_through_sequence: int,
    project_index_refs: tuple[ProjectIndexContextRef, ...] = (),
    review_output_id: str | None = None,
    artifact_refs: tuple[ArtifactContextRef, ...] = (),
    attachment_refs: tuple[AttachmentContextRef, ...] = (),
) -> ContextSnapshot:
    """创建只包含稳定引用的不可变 ContextSnapshot。"""
    _require_non_empty(
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        user_message_id=user_message_id,
    )
    if history_through_sequence < 0:
        raise ValueError("history_through_sequence 不能小于 0")
    if review_output_id is not None and not review_output_id.strip():
        raise ValueError("review_output_id 不能是空字符串")
    _reject_duplicate_refs(project_index_refs, "Project Index")
    _reject_duplicate_refs(artifact_refs, "Artifact")
    _reject_duplicate_refs(attachment_refs, "Attachment")
    if len(attachment_refs) > AGENT_MESSAGE_MAX_ATTACHMENTS:
        raise ValueError(f"每轮最多引用 {AGENT_MESSAGE_MAX_ATTACHMENTS} 个附件")
    schema_version = "agent-context.v2"
    hash_payload = {
        "schema_version": schema_version,
        "owner_id": owner_id,
        "project_id": project_id,
        "session_id": session_id,
        "turn_run_id": turn_run_id,
        "user_message_id": user_message_id,
        "history_through_sequence": history_through_sequence,
        "project_index_refs": [
            {
                "paper_id": ref.paper_id,
                "paper_version_id": ref.paper_version_id,
                "chunk_set_id": ref.chunk_set_id,
            }
            for ref in project_index_refs
        ],
        "review_output_id": review_output_id,
        "artifact_refs": [
            {"artifact_id": ref.artifact_id, "content_hash": ref.content_hash}
            for ref in artifact_refs
        ],
        "attachment_refs": [
            {
                "attachment_id": ref.attachment_id,
                "version": ref.version,
                "content_hash": ref.content_hash,
                "size_bytes": ref.size_bytes,
                "media_type": ref.media_type,
                "display_name": ref.display_name,
            }
            for ref in attachment_refs
        ],
    }
    return ContextSnapshot(
        snapshot_id=str(uuid4()),
        schema_version=schema_version,
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        user_message_id=user_message_id,
        history_through_sequence=history_through_sequence,
        project_index_refs=tuple(project_index_refs),
        review_output_id=review_output_id,
        artifact_refs=tuple(artifact_refs),
        attachment_refs=tuple(attachment_refs),
        snapshot_hash=_canonical_hash(hash_payload),
        created_at=datetime.now(UTC),
    )


def create_policy_snapshot(
    *,
    owner_id: str,
    project_id: str,
    session_id: str,
    turn_run_id: str,
    max_model_calls: int,
    max_tool_calls: int,
    policy_version: str = "agent-policy.v1",
    allowed_tool_names: tuple[str, ...] = (),
    allowed_skill_names: tuple[str, ...] = (),
    skill_refs: tuple[SkillPolicyRef, ...] = (),
    mcp_refs: tuple[McpPolicyRef, ...] = (),
    network_enabled: bool = False,
    sandbox_enabled: bool = False,
    approval_required: bool = True,
) -> PolicySnapshot:
    """创建不可变 PolicySnapshot；首版能力开关默认关闭。"""
    _require_non_empty(
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        policy_version=policy_version,
    )
    if max_model_calls < 0 or max_tool_calls < 0:
        raise ValueError("调用预算不能小于 0")
    _reject_duplicate_names(allowed_tool_names, "Tool")
    _reject_duplicate_names(allowed_skill_names, "Skill")
    skill_names = tuple(ref.name for ref in skill_refs)
    _reject_duplicate_names(skill_names, "Skill Policy")
    if set(skill_names) != set(allowed_skill_names):
        raise ValueError("Skill Policy 引用必须与 Skill allowlist 一致")
    required_skill_tools = {
        tool_name for ref in skill_refs for tool_name in ref.required_tool_names
    }
    if not required_skill_tools.issubset(allowed_tool_names):
        raise ValueError("Skill 所需 Tool 必须包含在 Policy Tool allowlist")
    mcp_tool_names = tuple(tool.name for ref in mcp_refs for tool in ref.tools)
    _reject_duplicate_names(mcp_tool_names, "MCP Tool")
    if not set(mcp_tool_names).issubset(allowed_tool_names):
        raise ValueError("MCP Tool 必须包含在 Policy Tool allowlist")
    hash_payload = {
        "policy_version": policy_version,
        "owner_id": owner_id,
        "project_id": project_id,
        "session_id": session_id,
        "turn_run_id": turn_run_id,
        "allowed_tool_names": list(allowed_tool_names),
        "allowed_skill_names": list(allowed_skill_names),
        "skill_refs": [
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
            for ref in skill_refs
        ],
        "mcp_refs": [
            {
                "profile_id": ref.profile_id,
                "profile_revision": ref.profile_revision,
                "catalog_id": ref.catalog_id,
                "version": ref.version,
                "config_hash": ref.config_hash,
                "tools": [
                    {"name": tool.name, "input_schema_hash": tool.input_schema_hash}
                    for tool in ref.tools
                ],
            }
            for ref in mcp_refs
        ],
        "network_enabled": network_enabled,
        "sandbox_enabled": sandbox_enabled,
        "approval_required": approval_required,
        "max_model_calls": max_model_calls,
        "max_tool_calls": max_tool_calls,
    }
    return PolicySnapshot(
        snapshot_id=str(uuid4()),
        policy_version=policy_version,
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        allowed_tool_names=tuple(allowed_tool_names),
        allowed_skill_names=tuple(allowed_skill_names),
        skill_refs=tuple(skill_refs),
        mcp_refs=tuple(mcp_refs),
        network_enabled=network_enabled,
        sandbox_enabled=sandbox_enabled,
        approval_required=approval_required,
        max_model_calls=max_model_calls,
        max_tool_calls=max_tool_calls,
        snapshot_hash=_canonical_hash(hash_payload),
        created_at=datetime.now(UTC),
    )


def create_project_research_workspace_policy_snapshot(
    *,
    owner_id: str,
    project_id: str,
    session_id: str,
    turn_run_id: str,
    mcp_refs: tuple[McpPolicyRef, ...] = (),
    skill_refs: tuple[SkillPolicyRef, ...] = (),
) -> PolicySnapshot:
    """由服务端选择 Slice 7.1 唯一固定的可执行研究能力档案。"""
    return create_policy_snapshot(
        owner_id=owner_id,
        project_id=project_id,
        session_id=session_id,
        turn_run_id=turn_run_id,
        policy_version=(
            PROJECT_RESEARCH_CAPABILITIES_POLICY_VERSION
            if skill_refs
            else PROJECT_RESEARCH_WORKSPACE_MCP_POLICY_VERSION
            if mcp_refs
            else PROJECT_RESEARCH_WORKSPACE_POLICY_VERSION
        ),
        allowed_tool_names=PROJECT_RESEARCH_WORKSPACE_TOOLS
        + tuple(tool.name for ref in mcp_refs for tool in ref.tools),
        allowed_skill_names=tuple(ref.name for ref in skill_refs),
        skill_refs=skill_refs,
        mcp_refs=mcp_refs,
        network_enabled=False,
        sandbox_enabled=True,
        approval_required=False,
        max_model_calls=8,
        max_tool_calls=12,
    )


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_non_empty(**values: str) -> None:
    for name, value in values.items():
        if not value.strip():
            raise ValueError(f"{name} 不能为空")


def _require_max_length(**values: tuple[str, int]) -> None:
    for name, (value, maximum) in values.items():
        if len(value) > maximum:
            raise ValueError(f"{name} 长度不能超过 {maximum}")


def _reject_duplicate_refs(refs: tuple[object, ...], label: str) -> None:
    if len(refs) != len(set(refs)):
        raise ValueError(f"{label} 引用不能重复")


def _reject_duplicate_names(names: tuple[str, ...], label: str) -> None:
    if any(not name.strip() for name in names):
        raise ValueError(f"{label} 名称不能为空")
    if len(names) != len(set(names)):
        raise ValueError(f"{label} 名称不能重复")


def _validate_attachment_ids(attachment_ids: tuple[str, ...]) -> None:
    if len(attachment_ids) > AGENT_MESSAGE_MAX_ATTACHMENTS:
        raise ValueError(f"每条消息最多引用 {AGENT_MESSAGE_MAX_ATTACHMENTS} 个附件")
    if any(not value.strip() or len(value) > 36 for value in attachment_ids):
        raise ValueError("attachment_id 非法")
    if len(attachment_ids) != len(set(attachment_ids)):
        raise ValueError("attachment_ids 不能重复")
