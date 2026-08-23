"""API 层共享依赖。"""

from typing import Annotated

from fastapi import Depends

from literature_agent.domain.actor import ActorContext
from literature_agent.infrastructure.auth.actor_provider import get_actor
from literature_agent.observability import current_correlation_id

__all__ = ["ActorDep", "CorrelationIdDep", "get_actor", "get_correlation_id"]

ActorDep = Annotated[ActorContext, Depends(get_actor)]


def get_correlation_id() -> str:
    """从请求中间件绑定的上下文读取关联标识。"""
    return current_correlation_id()


CorrelationIdDep = Annotated[str, Depends(get_correlation_id)]
