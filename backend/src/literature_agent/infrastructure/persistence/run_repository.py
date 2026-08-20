"""Run Repository 的 PostgreSQL 适配器。"""

from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.domain.run import ACTIVE_RUN_STATUSES, Run, RunStatus
from literature_agent.infrastructure.persistence.models import RunORM


def _to_domain(orm: RunORM) -> Run:
    """将 ORM 模型转换为领域实体。"""
    return Run(
        run_id=orm.run_id,
        project_id=orm.project_id,
        owner_id=orm.owner_id,
        run_type=orm.run_type,
        status=RunStatus(orm.status),
        input_payload=orm.input_payload,
        result_payload=orm.result_payload,
        event_sequence=orm.event_sequence,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def _to_orm(run: Run) -> RunORM:
    """将领域实体转换为 ORM 模型。"""
    return RunORM(
        run_id=run.run_id,
        project_id=run.project_id,
        owner_id=run.owner_id,
        run_type=run.run_type,
        status=run.status.value,
        input_payload=run.input_payload,
        result_payload=run.result_payload,
        event_sequence=run.event_sequence,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


class SqlalchemyRunRepository(RunRepository):
    """基于 SQLAlchemy AsyncSession 的 RunRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, run: Run) -> Run:
        """保存 Run。"""
        self._session.add(_to_orm(run))
        return run

    async def get_by_id(self, run_id: str) -> Run | None:
        """按 ID 查询 Run。"""
        result = await self._session.execute(
            select(RunORM).where(RunORM.run_id == run_id),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def get_by_id_for_update(self, run_id: str, owner_id: str) -> Run | None:
        """按 ID 查询并锁定 Run，同时校验所有者。"""
        result = await self._session.execute(
            select(RunORM)
            .where(RunORM.run_id == run_id, RunORM.owner_id == owner_id)
            .with_for_update(),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def update_status(
        self,
        run_id: str,
        expected_status: RunStatus,
        new_status: RunStatus,
        new_event_sequence: int,
    ) -> bool:
        """条件更新 Run 状态与 event_sequence。"""
        result = cast(
            CursorResult,
            await self._session.execute(
                update(RunORM)
                .where(
                    RunORM.run_id == run_id,
                    RunORM.status == expected_status.value,
                )
                .values(
                    status=new_status.value,
                    event_sequence=new_event_sequence,
                ),
            ),
        )
        return result.rowcount == 1

    async def has_active_runs(self, project_id: str) -> bool:
        """判断 Project 是否存在非终态 Run。"""
        result = await self._session.execute(
            select(RunORM.run_id)
            .where(
                RunORM.project_id == project_id,
                RunORM.status.in_([status.value for status in ACTIVE_RUN_STATUSES]),
            )
            .limit(1),
        )
        return result.first() is not None
