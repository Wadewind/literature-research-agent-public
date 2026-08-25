"""Agent Project Tool effect 的 PostgreSQL Repository。"""

from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.tool_execution_repository import (
    ToolExecutionRepository,
)
from literature_agent.domain.tool_execution import (
    ToolErrorKind,
    ToolExecution,
    ToolExecutionStatus,
)
from literature_agent.infrastructure.persistence.models import AgentToolExecutionORM


class SqlalchemyToolExecutionRepository(ToolExecutionRepository):
    """原始 Tool 参数不入库，只保存 hash、状态和有界安全结果。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, value: ToolExecution) -> ToolExecution:
        self._session.add(_to_orm(value))
        return value

    async def get(self, effect_id: str) -> ToolExecution | None:
        row = await self._session.get(AgentToolExecutionORM, effect_id)
        return _to_domain(row) if row is not None else None

    async def list_by_turn(self, turn_run_id: str) -> list[ToolExecution]:
        rows = (
            (
                await self._session.execute(
                    select(AgentToolExecutionORM)
                    .where(AgentToolExecutionORM.turn_run_id == turn_run_id)
                    .order_by(AgentToolExecutionORM.created_at, AgentToolExecutionORM.effect_id)
                )
            )
            .scalars()
            .all()
        )
        return [_to_domain(row) for row in rows]

    async def count_by_turn(self, turn_run_id: str) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(AgentToolExecutionORM)
                .where(AgentToolExecutionORM.turn_run_id == turn_run_id)
            )
        ).scalar_one()

    async def save(
        self,
        value: ToolExecution,
        *,
        expected_status: ToolExecutionStatus,
        expected_attempt_count: int,
    ) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentToolExecutionORM)
                .where(
                    AgentToolExecutionORM.effect_id == value.effect_id,
                    AgentToolExecutionORM.status == expected_status.value,
                    AgentToolExecutionORM.attempt_count == expected_attempt_count,
                )
                .values(
                    status=value.status.value,
                    result_payload=value.result_payload,
                    result_hash=value.result_hash,
                    error_kind=value.error_kind.value if value.error_kind else None,
                    error_code=value.error_code,
                    safe_message=value.safe_message,
                    attempt_count=value.attempt_count,
                    updated_at=value.updated_at,
                )
            ),
        )
        return result.rowcount == 1


def _to_orm(value: ToolExecution) -> AgentToolExecutionORM:
    return AgentToolExecutionORM(
        effect_id=value.effect_id,
        turn_run_id=value.turn_run_id,
        tool_name=value.tool_name,
        args_hash=value.args_hash,
        status=value.status.value,
        result_payload=value.result_payload,
        result_hash=value.result_hash,
        error_kind=value.error_kind.value if value.error_kind else None,
        error_code=value.error_code,
        safe_message=value.safe_message,
        attempt_count=value.attempt_count,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _to_domain(row: AgentToolExecutionORM) -> ToolExecution:
    return ToolExecution(
        effect_id=row.effect_id,
        turn_run_id=row.turn_run_id,
        tool_name=row.tool_name,
        args_hash=row.args_hash,
        status=ToolExecutionStatus(row.status),
        result_payload=row.result_payload,
        result_hash=row.result_hash,
        error_kind=ToolErrorKind(row.error_kind) if row.error_kind else None,
        error_code=row.error_code,
        safe_message=row.safe_message,
        attempt_count=row.attempt_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
