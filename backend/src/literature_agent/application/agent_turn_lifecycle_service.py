"""AgentTurn 终态与 Session 活动指针的幂等收敛。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.run import RunStatus, RunType

TSession = TypeVar("TSession", bound=Session)

_TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


class AgentTurnLifecycleService[TSession: Session]:
    """只为终态 AgentTurn 幂等释放所属 Session 的活动 Turn。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        agent_repo_factory: Callable[[TSession], AgentRepository],
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._agent_repo_factory = agent_repo_factory

    async def release_if_terminal(self, run_id: str, status: RunStatus) -> None:
        """终态回调；重复调用或非 Agent Run 均无副作用。"""
        if status not in _TERMINAL_STATUSES:
            return
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
            if (
                run is None
                or run.status is not status
                or run.run_type != RunType.AGENT_TURN
            ):
                return
            agent_repo = self._agent_repo_factory(session)
            turn = await agent_repo.get_turn_scoped(run_id, run.owner_id)
            if turn is None:
                return
            await agent_repo.release_active_turn(turn.session_id, run_id)
            await session.commit()
