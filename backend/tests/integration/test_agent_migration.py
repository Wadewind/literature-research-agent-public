"""Phase 5 Agent 业务与 Runtime Execution 迁移往返验证。"""

import os
import subprocess

from testcontainers.community.postgres import PostgresContainer

from literature_agent.infrastructure.persistence.models import (
    AgentContextSnapshotORM,
    AgentRuntimeExecutionORM,
    AgentTurnRunORM,
)


def test_agent_turn_foreign_keys_close_the_business_fact_graph() -> None:
    """Turn 与 Snapshot 的业务引用必须由明确命名 FK 约束。"""
    turn_fks = {
        foreign_key.constraint.name for foreign_key in AgentTurnRunORM.__table__.foreign_keys
    }
    context_fks = {
        foreign_key.constraint.name
        for foreign_key in AgentContextSnapshotORM.__table__.foreign_keys
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


def test_agent_migration_upgrade_downgrade_upgrade_and_check() -> None:
    """在临时 PostgreSQL 上验证 head → -1 → head 与 schema check。"""
    with PostgresContainer("pgvector/pgvector:pg18") as postgres:
        url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://"
        )
        env = {**os.environ, "DATABASE_URL": url}
        for args in (
            ("alembic", "upgrade", "head"),
            ("alembic", "downgrade", "-1"),
            ("alembic", "upgrade", "head"),
            ("alembic", "check"),
        ):
            result = subprocess.run(args, env=env, capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
