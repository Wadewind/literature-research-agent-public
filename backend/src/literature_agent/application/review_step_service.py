"""Review LangGraph 节点的幂等 RunStep 写入边界。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.exceptions import IdempotencyConflictError, RunNotFoundError
from literature_agent.domain.review import ReviewStepKey, RunStep, create_run_step


class ReviewStepService[TSession: Session]:
    """在授权范围内按稳定幂等键创建或复用 RunStep。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        review_repo_factory: Callable[[TSession], ReviewRepository],
    ) -> None:
        self._session_factory = session_factory
        self._review_repo_factory = review_repo_factory

    async def ensure_step(
        self,
        *,
        run_id: str,
        project_id: str,
        owner_id: str,
        step_key: ReviewStepKey,
        sequence: int,
        idempotency_key: str,
        input_refs: dict | None = None,
    ) -> RunStep:
        """提交一次短事务；节点重放返回同一 Step ID。"""
        async with self._session_factory() as session:
            repository = self._review_repo_factory(session)
            review = await repository.get_review_run_scoped(run_id, project_id, owner_id)
            if review is None:
                raise RunNotFoundError(run_id)
            proposed = create_run_step(
                run_id=run_id,
                step_key=step_key,
                sequence=sequence,
                idempotency_key=idempotency_key,
                input_refs=input_refs,
            )
            step = await repository.get_or_add_step(proposed)
            if (
                step.step_key != proposed.step_key
                or step.sequence != proposed.sequence
                or step.input_refs != proposed.input_refs
            ):
                raise IdempotencyConflictError(idempotency_key)
            await session.commit()
            return step
