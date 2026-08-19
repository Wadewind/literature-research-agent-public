"""API 层共享依赖。"""

from typing import Annotated

from fastapi import Depends

from literature_agent.domain.actor import ActorContext
from literature_agent.infrastructure.auth.actor_provider import get_actor

__all__ = ["ActorDep", "get_actor"]

ActorDep = Annotated[ActorContext, Depends(get_actor)]
