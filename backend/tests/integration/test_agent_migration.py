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
    AgentContextSnapshotORM,
    AgentMcpProfileORM,
    AgentOwnerSkillORM,
    AgentOwnerSkillVersionORM,
    AgentPolicySnapshotORM,
    AgentRuntimeExecutionORM,
    AgentSandboxLeaseORM,
    AgentSkillProfileORM,
    AgentTurnRunORM,
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
    assert {
        "path",
        "frontmatter",
        "script",
        "binary",
        "env",
        "secret",
    }.isdisjoint(AgentOwnerSkillVersionORM.__table__.columns.keys())


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

        run_alembic("upgrade", "head")
        state_check = candidate_checks()["ck_agent_candidate_state_fields"]
        assert all(
            value in state_check
            for value in ("staged", "validated", "committed", "rejected")
        )
        assert "sandbox_generation > 0" in state_check
        assert "sandbox_fencing_token > 0" in state_check
        run_alembic("downgrade", "-1")
        assert "ck_agent_candidate_state_fields" not in candidate_checks()
        run_alembic("upgrade", "head")
        assert "ck_agent_candidate_state_fields" in candidate_checks()
        run_alembic("check")
