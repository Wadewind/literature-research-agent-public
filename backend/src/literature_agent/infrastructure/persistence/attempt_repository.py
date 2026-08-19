"""Run Attempt Repository 的 PostgreSQL 适配器。"""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.attempt_repository import AttemptRepository
from literature_agent.domain.run import RunStatus
from literature_agent.domain.run_attempt import AttemptStatus, RunAttempt
from literature_agent.infrastructure.persistence.models import RunAttemptORM, RunORM


def _to_domain(orm: RunAttemptORM) -> RunAttempt:
    """将 ORM 模型转换为领域实体。"""
    return RunAttempt(
        attempt_id=orm.attempt_id,
        run_id=orm.run_id,
        attempt_number=orm.attempt_number,
        worker_id=orm.worker_id,
        status=AttemptStatus(orm.status),
        started_at=orm.started_at,
        heartbeat_at=orm.heartbeat_at,
        finished_at=orm.finished_at,
        error=orm.error,
    )


def _to_orm(attempt: RunAttempt) -> RunAttemptORM:
    """将领域实体转换为 ORM 模型。"""
    return RunAttemptORM(
        attempt_id=attempt.attempt_id,
        run_id=attempt.run_id,
        attempt_number=attempt.attempt_number,
        worker_id=attempt.worker_id,
        status=attempt.status.value,
        started_at=attempt.started_at,
        heartbeat_at=attempt.heartbeat_at,
        finished_at=attempt.finished_at,
        error=attempt.error,
    )


class SqlalchemyAttemptRepository(AttemptRepository):
    """基于 SQLAlchemy AsyncSession 的 AttemptRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, attempt: RunAttempt) -> RunAttempt:
        """保存 Attempt。"""
        self._session.add(_to_orm(attempt))
        return attempt

    async def count_by_run(self, run_id: str) -> int:
        """统计一个 Run 的 Attempt 数量。"""
        result = await self._session.execute(
            select(func.count())
            .select_from(RunAttemptORM)
            .where(RunAttemptORM.run_id == run_id),
        )
        return int(result.scalar_one())

    async def get_latest_by_run(self, run_id: str) -> RunAttempt | None:
        """查询一个 Run 最新的 Attempt。"""
        result = await self._session.execute(
            select(RunAttemptORM)
            .where(RunAttemptORM.run_id == run_id)
            .order_by(RunAttemptORM.attempt_number.desc())
            .limit(1),
        )
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def record_heartbeat(self, attempt_id: str, now: datetime) -> bool:
        """条件更新心跳时间；仅 RUNNING 状态可更新。"""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        result = cast(
            CursorResult,
            await self._session.execute(
                update(RunAttemptORM)
                .where(
                    RunAttemptORM.attempt_id == attempt_id,
                    RunAttemptORM.status == AttemptStatus.RUNNING.value,
                )
                .values(heartbeat_at=now),
            ),
        )
        return result.rowcount == 1

    async def finish_if_running(
        self,
        attempt_id: str,
        status: AttemptStatus,
        now: datetime,
        error: dict | None = None,
    ) -> bool:
        """条件结束 Attempt；仅 RUNNING 状态可更新。"""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        result = cast(
            CursorResult,
            await self._session.execute(
                update(RunAttemptORM)
                .where(
                    RunAttemptORM.attempt_id == attempt_id,
                    RunAttemptORM.status == AttemptStatus.RUNNING.value,
                )
                .values(
                    status=status.value,
                    heartbeat_at=now,
                    finished_at=now,
                    error=error,
                ),
            ),
        )
        return result.rowcount == 1

    async def list_expired_running(self, cutoff: datetime, limit: int) -> list[RunAttempt]:
        """查询 lease 过期的执行中 Attempt（关联 Run 仍 RUNNING）。"""
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        result = await self._session.execute(
            select(RunAttemptORM)
            .join(RunORM, RunORM.run_id == RunAttemptORM.run_id)
            .where(
                RunAttemptORM.status == AttemptStatus.RUNNING.value,
                RunAttemptORM.heartbeat_at < cutoff,
                RunORM.status == RunStatus.RUNNING.value,
            )
            .order_by(RunAttemptORM.heartbeat_at)
            .limit(limit),
        )
        return [_to_domain(orm) for orm in result.scalars().all()]
