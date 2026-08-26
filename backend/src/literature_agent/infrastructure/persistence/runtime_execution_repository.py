"""Runtime Execution 的 PostgreSQL Repository。"""

from typing import cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.runtime_execution_repository import (
    RuntimeExecutionRepository,
)
from literature_agent.domain.runtime_execution import RuntimeControlState, RuntimeExecution
from literature_agent.infrastructure.persistence.models import AgentRuntimeExecutionORM


class SqlalchemyRuntimeExecutionRepository(RuntimeExecutionRepository):
    """使用 Turn 唯一约束和行锁串行化认领，不保存 SDK 类型或正文。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, turn_run_id: str) -> RuntimeExecution | None:
        row = await self._session.get(AgentRuntimeExecutionORM, turn_run_id)
        return _to_domain(row) if row is not None else None

    async def get_for_update(self, turn_run_id: str) -> RuntimeExecution | None:
        row = (
            await self._session.execute(
                select(AgentRuntimeExecutionORM)
                .where(AgentRuntimeExecutionORM.turn_run_id == turn_run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def add_if_absent(self, execution: RuntimeExecution) -> bool:
        result = await self._session.execute(
            insert(AgentRuntimeExecutionORM)
            .values(**_values(execution))
            .on_conflict_do_nothing(index_elements=["turn_run_id"])
            .returning(AgentRuntimeExecutionORM.turn_run_id)
        )
        return result.scalar_one_or_none() is not None

    async def save(
        self, execution: RuntimeExecution, *, expected: RuntimeExecution
    ) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentRuntimeExecutionORM)
                .where(
                    AgentRuntimeExecutionORM.turn_run_id == execution.turn_run_id,
                    AgentRuntimeExecutionORM.state == expected.state.value,
                    AgentRuntimeExecutionORM.fencing_token == expected.fencing_token,
                    AgentRuntimeExecutionORM.current_attempt_id
                    == expected.current_attempt_id,
                    AgentRuntimeExecutionORM.lease_owner_id
                    == expected.lease_owner_id,
                )
                .values(**_values(execution))
            ),
        )
        return result.rowcount == 1


def _values(value: RuntimeExecution) -> dict[str, object]:
    return {
        "turn_run_id": value.turn_run_id,
        "session_id": value.session_id,
        "runtime_execution_id": value.runtime_execution_id,
        "request_hash": value.request_hash,
        "runtime_revision": value.runtime_revision,
        "graph_revision": value.graph_revision,
        "deepagents_version": value.deepagents_version,
        "langgraph_version": value.langgraph_version,
        "state": value.state.value,
        "fencing_token": value.fencing_token,
        "current_attempt_id": value.current_attempt_id,
        "lease_owner_id": value.lease_owner_id,
        "lease_expires_at": value.lease_expires_at,
        "last_checkpoint_id": value.last_checkpoint_id,
        "last_error_kind": value.last_error_kind,
        "last_error_code": value.last_error_code,
        "last_safe_message": value.last_safe_message,
        "started_at": value.started_at,
        "updated_at": value.updated_at,
        "finished_at": value.finished_at,
    }


def _to_domain(row: AgentRuntimeExecutionORM) -> RuntimeExecution:
    return RuntimeExecution(
        turn_run_id=row.turn_run_id,
        session_id=row.session_id,
        runtime_execution_id=row.runtime_execution_id,
        request_hash=row.request_hash,
        runtime_revision=row.runtime_revision,
        graph_revision=row.graph_revision,
        deepagents_version=row.deepagents_version,
        langgraph_version=row.langgraph_version,
        state=RuntimeControlState(row.state),
        fencing_token=row.fencing_token,
        current_attempt_id=row.current_attempt_id,
        lease_owner_id=row.lease_owner_id,
        lease_expires_at=row.lease_expires_at,
        last_checkpoint_id=row.last_checkpoint_id,
        last_error_kind=row.last_error_kind,
        last_error_code=row.last_error_code,
        last_safe_message=row.last_safe_message,
        started_at=row.started_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )
