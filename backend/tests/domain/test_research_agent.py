"""Research Agent Session/Message/Turn/Snapshot 领域契约测试。"""

from dataclasses import FrozenInstanceError

import pytest

from literature_agent.domain.research_agent import (
    AgentMessageRole,
    AgentSessionStatus,
    ArtifactContextRef,
    ProjectIndexContextRef,
    RuntimeSessionBinding,
    RuntimeTurnBinding,
    claim_active_turn,
    create_agent_artifact_candidate,
    create_agent_message,
    create_agent_session,
    create_agent_turn_run,
    create_context_snapshot,
    create_policy_snapshot,
    release_active_turn,
)
from literature_agent.domain.run import RunType, create_run


def test_agent_session_is_project_scoped_and_has_single_active_turn() -> None:
    """Session 固定 owner/Project，活动 Turn 必须先认领后释放。"""
    session = create_agent_session(
        owner_id="owner-1",
        project_id="project-1",
        title="GNN 研究",
    )

    assert session.owner_id == "owner-1"
    assert session.project_id == "project-1"
    assert session.status is AgentSessionStatus.ACTIVE
    assert session.active_turn_run_id is None

    claimed = claim_active_turn(session, "turn-1")
    assert claimed.active_turn_run_id == "turn-1"
    with pytest.raises(ValueError, match="已有活动 Turn"):
        claim_active_turn(claimed, "turn-2")

    assert release_active_turn(claimed, "turn-1").active_turn_run_id is None
    with pytest.raises(ValueError, match="不匹配"):
        release_active_turn(claimed, "turn-other")


def test_agent_message_requires_next_session_sequence_and_matching_turn() -> None:
    """消息按 Session 严格递增，且关联当前 Session/Turn。"""
    message = create_agent_message(
        session_id="session-1",
        last_sequence=0,
        role=AgentMessageRole.USER,
        content="比较两类方法",
        turn_run_id="turn-1",
        idempotency_key="message-1",
    )

    assert message.sequence == 1
    assert message.turn_run_id == "turn-1"
    next_message = create_agent_message(
        session_id="session-1",
        last_sequence=message.sequence,
        role=AgentMessageRole.ASSISTANT,
        content="结论",
        turn_run_id="turn-1",
        idempotency_key="message-2",
    )
    assert next_message.sequence == 2
    with pytest.raises(ValueError, match="last_sequence"):
        create_agent_message(
            session_id="session-1",
            last_sequence=-1,
            role=AgentMessageRole.ASSISTANT,
            content="结论",
            turn_run_id="turn-1",
            idempotency_key="message-2",
        )
    with pytest.raises(ValueError, match="不能为空"):
        create_agent_message(
            session_id="session-1",
            last_sequence=1,
            role=AgentMessageRole.ASSISTANT,
            content=" ",
            turn_run_id="turn-1",
            idempotency_key="message-2",
        )


def test_turn_run_links_user_message_and_immutable_snapshots() -> None:
    """Turn 扩展记录只关联同一 Session 的用户消息和不可变快照。"""
    turn = create_agent_turn_run(
        turn_run_id="turn-1",
        session_id="session-1",
        user_message_id="message-1",
        context_snapshot_id="context-1",
        policy_snapshot_id="policy-1",
    )

    assert turn.turn_run_id == "turn-1"
    assert turn.user_message_id == "message-1"
    with pytest.raises(FrozenInstanceError):
        turn.session_id = "session-2"  # type: ignore[misc]


def test_context_snapshot_keeps_only_stable_refs_and_review_output_id() -> None:
    """上下文固化版本引用和历史边界，不复制 Matrix payload。"""
    snapshot = create_context_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        user_message_id="message-1",
        history_through_sequence=4,
        project_index_refs=(
            ProjectIndexContextRef(
                paper_id="paper-1",
                paper_version_id="version-1",
                chunk_set_id="chunk-set-1",
            ),
        ),
        review_output_id="review-output-1",
        artifact_refs=(ArtifactContextRef(artifact_id="artifact-1", content_hash="a" * 64),),
    )

    assert snapshot.review_output_id == "review-output-1"
    assert snapshot.history_through_sequence == 4
    assert snapshot.project_index_refs[0].chunk_set_id == "chunk-set-1"
    assert snapshot.snapshot_hash
    assert not hasattr(snapshot, "review_run_id")
    assert not hasattr(snapshot, "evidence_matrix")
    with pytest.raises(FrozenInstanceError):
        snapshot.review_output_id = "review-output-2"  # type: ignore[misc]


def test_context_snapshot_rejects_duplicate_or_invalid_refs() -> None:
    """重复版本引用和非法哈希不能进入不可变上下文。"""
    duplicate = ProjectIndexContextRef(
        paper_id="paper-1",
        paper_version_id="version-1",
        chunk_set_id="chunk-set-1",
    )
    with pytest.raises(ValueError, match="重复"):
        create_context_snapshot(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            user_message_id="message-1",
            history_through_sequence=0,
            project_index_refs=(duplicate, duplicate),
        )
    with pytest.raises(ValueError, match="SHA-256"):
        ArtifactContextRef(artifact_id="artifact-1", content_hash="bad")


def test_snapshot_hashes_are_stable_for_equal_authorization_facts() -> None:
    """Snapshot ID/时间不同不影响同一授权事实的内容哈希。"""
    context_args = {
        "owner_id": "owner-1",
        "project_id": "project-1",
        "session_id": "session-1",
        "turn_run_id": "turn-1",
        "user_message_id": "message-1",
        "history_through_sequence": 2,
        "review_output_id": "review-output-1",
    }
    policy_args = {
        "owner_id": "owner-1",
        "project_id": "project-1",
        "session_id": "session-1",
        "turn_run_id": "turn-1",
        "max_model_calls": 2,
        "max_tool_calls": 0,
    }

    assert (
        create_context_snapshot(**context_args).snapshot_hash
        == create_context_snapshot(**context_args).snapshot_hash
    )
    assert (
        create_policy_snapshot(**policy_args).snapshot_hash
        == create_policy_snapshot(**policy_args).snapshot_hash
    )


def test_policy_snapshot_is_immutable_and_defaults_to_denied_capabilities() -> None:
    """首版策略默认禁网、禁 Sandbox、无 Tool/Skill，并固化预算。"""
    snapshot = create_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id="turn-1",
        max_model_calls=2,
        max_tool_calls=3,
    )

    assert snapshot.allowed_tool_names == ()
    assert snapshot.allowed_skill_names == ()
    assert snapshot.network_enabled is False
    assert snapshot.sandbox_enabled is False
    assert snapshot.approval_required is True
    assert snapshot.snapshot_hash
    with pytest.raises(FrozenInstanceError):
        snapshot.max_tool_calls = 4  # type: ignore[misc]


def test_runtime_bindings_are_opaque_and_scoped() -> None:
    """业务绑定只保存 opaque ID，不暴露任何 SDK 对象。"""
    session_binding = RuntimeSessionBinding(
        session_id="session-1",
        binding_id="binding-1",
        generation=1,
        runtime_thread_id="opaque-thread",
        runtime_workspace_id="opaque-workspace",
    )
    turn_binding = RuntimeTurnBinding(
        session_id="session-1",
        turn_run_id="turn-1",
        session_binding_id="binding-1",
        runtime_execution_id="opaque-execution",
        runtime_checkpoint_id="opaque-checkpoint",
    )

    assert session_binding.binding_id == "binding-1"
    assert session_binding.generation == 1
    assert session_binding.runtime_thread_id == "opaque-thread"
    assert turn_binding.session_binding_id == session_binding.binding_id
    assert turn_binding.runtime_checkpoint_id == "opaque-checkpoint"


def test_runtime_binding_rejects_invalid_generation_or_empty_session_binding_ref() -> None:
    """Binding generation 必须为正数，Turn 必须指向具体 Session Binding。"""
    with pytest.raises(ValueError, match="generation"):
        RuntimeSessionBinding(
            session_id="session-1",
            binding_id="binding-1",
            generation=0,
            runtime_thread_id="opaque-thread",
            runtime_workspace_id="opaque-workspace",
        )
    with pytest.raises(ValueError, match="session_binding_id"):
        RuntimeTurnBinding(
            session_id="session-1",
            turn_run_id="turn-1",
            session_binding_id="",
            runtime_execution_id="opaque-execution",
            runtime_checkpoint_id="opaque-checkpoint",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_id", "", "candidate_id"),
        ("candidate_id", "c" * 256, "candidate_id"),
        ("name", " ", "name"),
        ("name", "n" * 256, "name"),
        ("media_type", "", "media_type"),
        ("media_type", "m" * 256, "media_type"),
        ("content_ref", "", "content_ref"),
        ("content_ref", "r" * 501, "content_ref"),
        ("content_hash", "bad", "SHA-256"),
        ("size_bytes", -1, "size_bytes"),
        ("size_bytes", 1_000_001, "size_bytes"),
    ],
)
def test_artifact_candidate_rejects_unbounded_or_invalid_runtime_metadata(
    field: str,
    value: str | int,
    message: str,
) -> None:
    """不可信 Runtime descriptor 必须先通过领域边界再进入结果事务。"""
    values: dict[str, str | int] = {
        "candidate_id": "candidate-1",
        "name": "notes.md",
        "media_type": "text/markdown",
        "content_ref": "runtime://candidate-1",
        "content_hash": "a" * 64,
        "size_bytes": 12,
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        create_agent_artifact_candidate(
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            **values,  # type: ignore[arg-type]
        )


def test_artifact_candidate_accepts_boundary_sizes() -> None:
    """候选元数据允许空文件和切片 2 上限内的小型文件描述。"""
    for size_bytes in (0, 1_000_000):
        candidate = create_agent_artifact_candidate(
            candidate_id=f"candidate-{size_bytes}",
            owner_id="owner-1",
            project_id="project-1",
            session_id="session-1",
            turn_run_id="turn-1",
            name="notes.md",
            media_type="text/markdown",
            content_ref="runtime://notes",
            content_hash="a" * 64,
            size_bytes=size_bytes,
        )

        assert candidate.size_bytes == size_bytes


def test_agent_turn_is_a_supported_run_type_without_changing_state_machine() -> None:
    """通用 Run 接受 agent_turn，但仍从 queued 开始。"""
    run = create_run("project-1", "owner-1", RunType.AGENT_TURN)

    assert run.run_type == "agent_turn"
    assert run.status.value == "queued"
