"""Phase 5 Agent 业务与 Runtime Execution 迁移往返验证。"""

import os
import subprocess
import sys
from typing import cast

from sqlalchemy import Table, create_engine, inspect
from testcontainers.community.postgres import PostgresContainer

from literature_agent.infrastructure.persistence.models import (
    AgentArtifactCandidateORM,
    AgentArtifactORM,
    AgentBrowserControlLeaseORM,
    AgentContextSnapshotORM,
    AgentMcpProfileORM,
    AgentModelCallReservationORM,
    AgentOwnerSkillORM,
    AgentOwnerSkillVersionORM,
    AgentPolicySnapshotORM,
    AgentRuntimeExecutionORM,
    AgentSandboxCleanupORM,
    AgentSandboxLeaseORM,
    AgentSkillProfileORM,
    AgentToolCallORM,
    AgentTurnRunORM,
    AgentTurnUsageORM,
    AgentWorkspaceSnapshotORM,
)


def test_agent_artifact_is_independent_immutable_business_fact() -> None:
    """正式 AgentArtifact 独立于 Review Artifact，并闭合 Candidate/Turn/Session。"""
    candidate_columns = set(AgentArtifactCandidateORM.__table__.columns.keys())
    artifact_targets = {
        foreign_key.target_fullname
        for foreign_key in AgentArtifactORM.__table__.foreign_keys
    }
    assert {
        "tool_call_id",
        "storage_key",
        "sandbox_generation",
        "sandbox_fencing_token",
        "validated_at",
        "committed_at",
    } <= candidate_columns
    assert artifact_targets == {
        "agent_artifact_candidates.candidate_id",
        "agent_sessions.session_id",
        "agent_turn_runs.turn_run_id",
        "projects.project_id",
    }
    assert {
        constraint.name
        for constraint in AgentArtifactCandidateORM.__table__.constraints
    } >= {
        "ck_agent_candidate_status",
        "ck_agent_candidate_state_fields",
    }


def test_agent_turn_foreign_keys_close_the_business_fact_graph() -> None:
    """Turn 与 Snapshot 的业务引用必须由明确命名 FK 约束。"""
    turn_fks = {
        foreign_key.constraint.name
        for foreign_key in AgentTurnRunORM.__table__.foreign_keys
        if foreign_key.constraint is not None
    }
    context_fks = {
        foreign_key.constraint.name
        for foreign_key in AgentContextSnapshotORM.__table__.foreign_keys
        if foreign_key.constraint is not None
    }
    assert {
        "fk_agent_turn_runs_user_message",
        "fk_agent_turn_runs_context_snapshot",
        "fk_agent_turn_runs_policy_snapshot",
    } <= turn_fks
    assert "fk_agent_context_snapshots_user_message" in context_fks


def test_runtime_execution_references_turn_session_and_current_attempt() -> None:
    """Runtime 控制事实不替代 Turn、Session 或 RunAttempt。"""
    targets = {
        foreign_key.target_fullname
        for foreign_key in AgentRuntimeExecutionORM.__table__.foreign_keys
    }
    assert targets == {
        "agent_turn_runs.turn_run_id",
        "agent_sessions.session_id",
        "run_attempts.attempt_id",
    }
    assert {
        "request_hash",
        "runtime_revision",
        "graph_revision",
        "fencing_token",
        "lease_owner_id",
        "lease_expires_at",
        "last_checkpoint_id",
    } <= set(AgentRuntimeExecutionORM.__table__.columns.keys())


def test_sandbox_workspace_tables_reference_business_scope() -> None:
    """Lease/Snapshot 是平台事实，但物理 Sandbox 标识不进入业务 Port。"""
    lease_targets = {
        foreign_key.target_fullname
        for foreign_key in AgentSandboxLeaseORM.__table__.foreign_keys
    }
    snapshot_targets = {
        foreign_key.target_fullname
        for foreign_key in AgentWorkspaceSnapshotORM.__table__.foreign_keys
    }

    assert lease_targets == {
        "agent_sessions.session_id",
        "agent_turn_runs.turn_run_id",
        "projects.project_id",
    }
    assert snapshot_targets == {
        "agent_sessions.session_id",
        "agent_turn_runs.turn_run_id",
        "projects.project_id",
    }
    cleanup_targets = {
        foreign_key.target_fullname
        for foreign_key in AgentSandboxCleanupORM.__table__.foreign_keys
    }
    assert cleanup_targets == {
        "agent_sessions.session_id",
        "projects.project_id",
    }
    cleanup_columns = set(AgentSandboxCleanupORM.__table__.columns.keys())
    assert {
        "cleanup_id",
        "generation",
        "fencing_token",
        "attempt_count",
        "next_attempt_at",
        "last_error_code",
        "last_error_summary",
    } <= cleanup_columns
    assert {
        "endpoint",
        "command",
        "output",
        "secret",
    }.isdisjoint(cleanup_columns)


def test_browser_control_is_session_scoped_and_never_stores_raw_endpoint_or_ticket() -> None:
    targets = {
        foreign_key.target_fullname
        for foreign_key in AgentBrowserControlLeaseORM.__table__.foreign_keys
    }
    assert targets == {
        "agent_sessions.session_id",
        "agent_turn_runs.turn_run_id",
        "projects.project_id",
    }
    columns = set(AgentBrowserControlLeaseORM.__table__.columns.keys())
    assert {
        "sandbox_generation",
        "sandbox_fencing_token",
        "revision",
        "ticket_digest",
        "viewer_connection_id",
        "expires_at",
    } <= columns
    assert {
        "ticket",
        "sandbox_id",
        "endpoint",
        "vnc_url",
        "cdp_url",
    }.isdisjoint(columns)
    assert {
        constraint.name
        for constraint in AgentBrowserControlLeaseORM.__table__.constraints
    } >= {
        "ck_browser_control_status",
        "ck_browser_control_state_fields",
        "ck_browser_control_ttl",
        "uq_agent_browser_control_session_revision",
    }


def test_mcp_profile_and_policy_snapshot_remain_sdk_neutral() -> None:
    """MCP Profile 隔离到 Session，逐 Turn Policy 只保存冻结引用。"""
    targets = {
        foreign_key.target_fullname
        for foreign_key in AgentMcpProfileORM.__table__.foreign_keys
    }
    assert targets == {"agent_sessions.session_id"}
    assert {
        "profile_id",
        "session_id",
        "owner_id",
        "revision",
        "selections",
        "config_hash",
    } <= set(AgentMcpProfileORM.__table__.columns.keys())
    assert "mcp_refs" in AgentPolicySnapshotORM.__table__.columns
    profile_table = cast(Table, AgentMcpProfileORM.__table__)
    assert {column.name for column in profile_table.primary_key.columns} == {
        "profile_id",
        "revision",
    }
    assert {
        constraint.name for constraint in profile_table.constraints
    } >= {"uq_agent_mcp_profiles_session_revision"}
    assert {
        "url",
        "endpoint",
        "transport",
        "command",
        "env",
        "secret",
    }.isdisjoint(AgentMcpProfileORM.__table__.columns.keys())


def test_native_skill_versions_profiles_and_policy_refs_remain_sdk_neutral() -> None:
    """Skill 内容是 owner 业务事实，Profile 与逐 Turn 引用不保存 SDK path。"""
    identity_targets = {
        foreign_key.target_fullname
        for foreign_key in AgentOwnerSkillVersionORM.__table__.foreign_keys
    }
    profile_targets = {
        foreign_key.target_fullname
        for foreign_key in AgentSkillProfileORM.__table__.foreign_keys
    }
    assert identity_targets == {
        "agent_owner_skills.skill_id",
        "agent_owner_skills.owner_id",
    }
    assert profile_targets == {"agent_sessions.session_id"}
    assert {
        "skill_id",
        "owner_id",
        "name",
    } <= set(AgentOwnerSkillORM.__table__.columns.keys())
    assert {
        "skill_id",
        "version",
        "owner_id",
        "instructions",
        "required_tool_names",
        "content_hash",
    } <= set(AgentOwnerSkillVersionORM.__table__.columns.keys())
    assert "skill_refs" in AgentPolicySnapshotORM.__table__.columns
    assert "network_profile_id" in AgentPolicySnapshotORM.__table__.columns
    assert "network_profile_version" in AgentPolicySnapshotORM.__table__.columns
    assert "network_profile_hash" in AgentPolicySnapshotORM.__table__.columns
    assert "network_profile_id" in AgentSandboxLeaseORM.__table__.columns
    assert "network_profile_version" in AgentSandboxLeaseORM.__table__.columns
    assert "network_profile_hash" in AgentSandboxLeaseORM.__table__.columns
    assert "source_url" in AgentArtifactCandidateORM.__table__.columns
    assert "source_url_hash" in AgentArtifactCandidateORM.__table__.columns
    assert "source_url" in AgentArtifactORM.__table__.columns
    assert "source_url_hash" in AgentArtifactORM.__table__.columns
    assert {
        "path",
        "frontmatter",
        "script",
        "binary",
        "env",
        "secret",
    }.isdisjoint(AgentOwnerSkillVersionORM.__table__.columns.keys())


def test_agent_usage_and_tool_summary_tables_are_sdk_neutral_and_content_free() -> None:
    usage_targets = {
        foreign_key.target_fullname for foreign_key in AgentTurnUsageORM.__table__.foreign_keys
    }
    assert usage_targets == {
        "agent_turn_runs.turn_run_id",
        "agent_policy_snapshots.snapshot_id",
        "agent_sessions.session_id",
        "projects.project_id",
    }
    assert {
        foreign_key.target_fullname
        for foreign_key in AgentModelCallReservationORM.__table__.foreign_keys
    } == {"agent_turn_usages.turn_run_id"}
    assert {
        foreign_key.target_fullname for foreign_key in AgentToolCallORM.__table__.foreign_keys
    } == {"agent_turn_usages.turn_run_id"}
    columns = set(AgentToolCallORM.__table__.columns.keys())
    assert {"args_hash", "result_hash", "input_size_bytes", "output_size_bytes"} <= columns
    assert {
        "arguments",
        "result_payload",
        "endpoint",
        "prompt",
        "secret",
    }.isdisjoint(columns)


def test_agent_migration_upgrade_downgrade_upgrade_and_check() -> None:
    """在临时 PostgreSQL 上验证 head → -1 → head 与 schema check。"""
    with PostgresContainer("pgvector/pgvector:pg18") as postgres:
        url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://"
        )
        env = {**os.environ, "AGENT_DATABASE_URL": url}
        def run_alembic(*arguments: str) -> None:
            args = (sys.executable, "-m", "alembic", *arguments)
            result = subprocess.run(args, env=env, capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr

        def candidate_checks() -> dict[str, str]:
            engine = create_engine(url)
            try:
                return {
                    value["name"]: value["sqltext"]
                    for value in inspect(engine).get_check_constraints(
                        "agent_artifact_candidates"
                    )
                    if value["name"] is not None
                }
            finally:
                engine.dispose()

        def has_table(name: str) -> bool:
            engine = create_engine(url)
            try:
                return inspect(engine).has_table(name)
            finally:
                engine.dispose()

        def has_column(table_name: str, column_name: str) -> bool:
            engine = create_engine(url)
            try:
                return column_name in {
                    value["name"] for value in inspect(engine).get_columns(table_name)
                }
            finally:
                engine.dispose()

        def attachment_constraints() -> set[str]:
            engine = create_engine(url)
            try:
                inspector = inspect(engine)
                checks = {
                    value["name"]
                    for value in inspector.get_check_constraints("agent_attachments")
                    if value["name"] is not None
                }
                checks.update(
                    value["name"]
                    for value in inspector.get_check_constraints(
                        "agent_message_attachments"
                    )
                    if value["name"] is not None
                )
                return checks
            finally:
                engine.dispose()

        run_alembic("upgrade", "head")
        state_check = candidate_checks()["ck_agent_candidate_state_fields"]
        assert all(
            value in state_check
            for value in ("staged", "validated", "committed", "rejected")
        )
        assert "sandbox_generation > 0" in state_check
        assert "sandbox_fencing_token > 0" in state_check
        assert has_table("agent_browser_control_leases")
        assert has_table("agent_attachments")
        assert has_table("agent_message_attachments")
        assert has_table("agent_turn_usages")
        assert has_table("agent_model_call_reservations")
        assert has_table("agent_tool_calls")
        assert has_table("agent_sandbox_cleanups")
        assert has_column("agent_policy_snapshots", "network_profile_hash")
        assert {
            "ck_agent_attachment_version",
            "ck_agent_attachment_size",
            "ck_agent_attachment_status",
            "ck_agent_attachment_state_fields",
            "ck_agent_message_attachment_ordinal",
        } <= attachment_constraints()
        run_alembic("downgrade", "-1")
        assert has_table("agent_browser_control_leases")
        assert has_table("agent_attachments")
        assert has_table("agent_message_attachments")
        assert has_table("agent_turn_usages")
        assert has_table("agent_model_call_reservations")
        assert has_table("agent_tool_calls")
        assert has_table("agent_sandbox_cleanups")
        assert not has_column("agent_policy_snapshots", "network_profile_hash")
        assert "ck_agent_candidate_state_fields" in candidate_checks()
        run_alembic("downgrade", "-1")
        assert not has_table("agent_sandbox_cleanups")
        run_alembic("upgrade", "head")
        assert has_table("agent_browser_control_leases")
        assert has_table("agent_attachments")
        assert has_table("agent_turn_usages")
        assert has_table("agent_sandbox_cleanups")
        assert has_column("agent_policy_snapshots", "network_profile_hash")
        assert "ck_agent_candidate_state_fields" in candidate_checks()
        run_alembic("check")
