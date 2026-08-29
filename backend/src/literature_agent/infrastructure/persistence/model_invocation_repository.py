"""Model Invocation Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.model_invocation_repository import (
    ModelInvocationRepository,
)
from literature_agent.domain.model_invocation import (
    InvocationStatus,
    ModelCapability,
    ModelInvocation,
)
from literature_agent.domain.model_types import ChatFinishReason
from literature_agent.infrastructure.persistence.models import ModelInvocationORM


def _to_domain(orm: ModelInvocationORM) -> ModelInvocation:
    """将 ORM 模型转换为领域实体。"""
    return ModelInvocation(
        invocation_id=orm.invocation_id,
        run_id=orm.run_id,
        capability=ModelCapability(orm.capability),
        provider=orm.provider,
        model=orm.model,
        status=InvocationStatus(orm.status),
        latency_ms=orm.latency_ms,
        created_at=orm.created_at,
        prompt_tokens=orm.prompt_tokens,
        completion_tokens=orm.completion_tokens,
        error_type=orm.error_type,
        requested_max_tokens=orm.requested_max_tokens,
        finish_reason=(
            ChatFinishReason(orm.finish_reason) if orm.finish_reason is not None else None
        ),
        response_bytes=orm.response_bytes,
        response_sha256=orm.response_sha256,
    )


def _to_orm(invocation: ModelInvocation) -> ModelInvocationORM:
    """将领域实体转换为 ORM 模型。"""
    return ModelInvocationORM(
        invocation_id=invocation.invocation_id,
        run_id=invocation.run_id,
        capability=invocation.capability.value,
        provider=invocation.provider,
        model=invocation.model,
        status=invocation.status.value,
        prompt_tokens=invocation.prompt_tokens,
        completion_tokens=invocation.completion_tokens,
        latency_ms=invocation.latency_ms,
        error_type=invocation.error_type,
        requested_max_tokens=invocation.requested_max_tokens,
        finish_reason=(
            invocation.finish_reason.value if invocation.finish_reason is not None else None
        ),
        response_bytes=invocation.response_bytes,
        response_sha256=invocation.response_sha256,
        created_at=invocation.created_at,
    )


class SqlalchemyModelInvocationRepository(ModelInvocationRepository):
    """基于 SQLAlchemy AsyncSession 的 ModelInvocationRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add(self, invocation: ModelInvocation) -> ModelInvocation:
        """保存一条模型调用记录。"""
        self._session.add(_to_orm(invocation))
        return invocation

    async def list_by_run(self, run_id: str) -> list[ModelInvocation]:
        """按 Run 查询调用记录，按创建时间升序返回。"""
        result = await self._session.execute(
            select(ModelInvocationORM)
            .where(ModelInvocationORM.run_id == run_id)
            .order_by(ModelInvocationORM.created_at),
        )
        return [_to_domain(orm) for orm in result.scalars().all()]
