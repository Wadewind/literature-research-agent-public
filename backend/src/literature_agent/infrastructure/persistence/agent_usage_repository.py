"""Agent Turn Usage 的 PostgreSQL Repository。"""

from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.agent_usage_repository import AgentUsageRepository
from literature_agent.domain.agent_usage import (
    AgentModelCallReservation,
    AgentToolCall,
    AgentToolCallStatus,
    AgentTurnUsage,
)
from literature_agent.infrastructure.persistence.models import (
    AgentModelCallReservationORM,
    AgentToolCallORM,
    AgentTurnUsageORM,
)


class SqlalchemyAgentUsageRepository(AgentUsageRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_usage(self, value: AgentTurnUsage) -> AgentTurnUsage:
        self._session.add(_usage_to_orm(value))
        return value

    async def get_usage(self, turn_run_id: str) -> AgentTurnUsage | None:
        row = await self._session.get(AgentTurnUsageORM, turn_run_id)
        return _usage_to_domain(row) if row is not None else None

    async def get_usage_for_update(self, turn_run_id: str) -> AgentTurnUsage | None:
        row = (
            await self._session.execute(
                select(AgentTurnUsageORM)
                .where(AgentTurnUsageORM.turn_run_id == turn_run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        return _usage_to_domain(row) if row is not None else None

    async def save_usage(self, value: AgentTurnUsage) -> None:
        await self._session.execute(
            update(AgentTurnUsageORM)
            .where(AgentTurnUsageORM.turn_run_id == value.turn_run_id)
            .values(
                model_calls_reserved=value.model_calls_reserved,
                tool_calls_reserved=value.tool_calls_reserved,
                input_tokens=value.input_tokens,
                output_tokens=value.output_tokens,
                started_at=value.started_at,
                deadline_at=value.deadline_at,
                updated_at=value.updated_at,
            )
        )

    async def get_model_call(self, reservation_key: str) -> AgentModelCallReservation | None:
        row = await self._session.get(AgentModelCallReservationORM, reservation_key)
        return _model_call_to_domain(row) if row is not None else None

    async def add_model_call(self, value: AgentModelCallReservation) -> AgentModelCallReservation:
        self._session.add(_model_call_to_orm(value))
        return value

    async def save_model_call(self, value: AgentModelCallReservation) -> None:
        await self._session.execute(
            update(AgentModelCallReservationORM)
            .where(AgentModelCallReservationORM.reservation_key == value.reservation_key)
            .values(
                input_tokens=value.input_tokens,
                output_tokens=value.output_tokens,
                updated_at=value.updated_at,
            )
        )

    async def get_tool_call(self, reservation_key: str) -> AgentToolCall | None:
        row = await self._session.get(AgentToolCallORM, reservation_key)
        return _tool_call_to_domain(row) if row is not None else None

    async def add_tool_call(self, value: AgentToolCall) -> AgentToolCall:
        self._session.add(_tool_call_to_orm(value))
        return value

    async def list_tool_calls(self, turn_run_id: str) -> list[AgentToolCall]:
        rows = (
            (
                await self._session.execute(
                    select(AgentToolCallORM)
                    .where(AgentToolCallORM.turn_run_id == turn_run_id)
                    .order_by(AgentToolCallORM.created_at, AgentToolCallORM.reservation_key)
                )
            )
            .scalars()
            .all()
        )
        return [_tool_call_to_domain(row) for row in rows]

    async def count_tool_calls_by_signature(
        self, turn_run_id: str, tool_name: str, args_hash: str
    ) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(AgentToolCallORM)
                .where(
                    AgentToolCallORM.turn_run_id == turn_run_id,
                    AgentToolCallORM.tool_name == tool_name,
                    AgentToolCallORM.args_hash == args_hash,
                )
            )
        ).scalar_one()

    async def save_tool_call(
        self,
        value: AgentToolCall,
        *,
        expected_status: AgentToolCallStatus,
    ) -> bool:
        result = cast(
            CursorResult,
            await self._session.execute(
                update(AgentToolCallORM)
                .where(
                    AgentToolCallORM.reservation_key == value.reservation_key,
                    AgentToolCallORM.status == expected_status.value,
                )
                .values(
                    status=value.status.value,
                    output_size_bytes=value.output_size_bytes,
                    result_hash=value.result_hash,
                    error_code=value.error_code,
                    safe_message=value.safe_message,
                    duration_ms=value.duration_ms,
                    started_at=value.started_at,
                    completed_at=value.completed_at,
                    updated_at=value.updated_at,
                )
            ),
        )
        return result.rowcount == 1


def _usage_to_orm(value: AgentTurnUsage) -> AgentTurnUsageORM:
    return AgentTurnUsageORM(**{name: getattr(value, name) for name in value.__slots__})


def _usage_to_domain(row: AgentTurnUsageORM) -> AgentTurnUsage:
    return AgentTurnUsage(**{name: getattr(row, name) for name in AgentTurnUsage.__slots__})


def _model_call_to_orm(value: AgentModelCallReservation) -> AgentModelCallReservationORM:
    return AgentModelCallReservationORM(**{name: getattr(value, name) for name in value.__slots__})


def _model_call_to_domain(row: AgentModelCallReservationORM) -> AgentModelCallReservation:
    return AgentModelCallReservation(
        **{name: getattr(row, name) for name in AgentModelCallReservation.__slots__}
    )


def _tool_call_to_orm(value: AgentToolCall) -> AgentToolCallORM:
    payload = {name: getattr(value, name) for name in value.__slots__}
    payload["status"] = value.status.value
    return AgentToolCallORM(**payload)


def _tool_call_to_domain(row: AgentToolCallORM) -> AgentToolCall:
    payload = {name: getattr(row, name) for name in AgentToolCall.__slots__}
    payload["status"] = AgentToolCallStatus(row.status)
    return AgentToolCall(**payload)
