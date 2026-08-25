"""Review API 的 Project/owner scoped 只读查询。"""

import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import RunNotFoundError
from literature_agent.domain.review import Artifact, ReviewOutputType
from literature_agent.domain.run import RunType

TSession = TypeVar("TSession", bound=Session)


class ReviewQueryService[TSession: Session]:
    """集中执行 Review 对外读取的三元范围校验。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        run_repo_factory: Callable[[TSession], RunRepository],
        review_repo_factory: Callable[[TSession], ReviewRepository],
        storage: Storage,
    ) -> None:
        self._session_factory = session_factory
        self._run_repo_factory = run_repo_factory
        self._review_repo_factory = review_repo_factory
        self._storage = storage

    async def detail(self, actor: ActorContext, project_id: str, run_id: str):
        async with self._session_factory() as session:
            run = await self._run_repo_factory(session).get_by_id(run_id)
            repo = self._review_repo_factory(session)
            review = await repo.get_review_run_scoped(run_id, project_id, actor.owner_id)
            if (
                run is None
                or review is None
                or run.owner_id != actor.owner_id
                or run.project_id != project_id
                or run.run_type != RunType.REVIEW.value
            ):
                raise RunNotFoundError(run_id)
            steps = await repo.list_steps_scoped(run_id, project_id, actor.owner_id)
            request = await repo.get_open_human_input_request_scoped(
                run_id, project_id, actor.owner_id
            )
            return run, review, steps, request

    async def list_reviews(self, actor: ActorContext, project_id: str):
        """返回当前 owner/Project 下的 Review，越权 Project 表现为空列表。"""
        async with self._session_factory() as session:
            return await self._review_repo_factory(session).list_review_runs_scoped(
                project_id, actor.owner_id
            )

    async def sources(self, actor, project_id, run_id):
        await self.detail(actor, project_id, run_id)
        async with self._session_factory() as session:
            return await self._review_repo_factory(session).list_sources_scoped(
                run_id, project_id, actor.owner_id
            )

    async def output(self, actor, project_id, run_id, output_type, output_key):
        await self.detail(actor, project_id, run_id)
        async with self._session_factory() as session:
            outputs = await self._review_repo_factory(session).list_outputs_scoped(
                run_id, project_id, actor.owner_id
            )
            matches = [
                item
                for item in outputs
                if item.output_type is output_type and item.output_key == output_key
            ]
            return max(matches, key=lambda item: item.version) if matches else None

    async def sections(self, actor, project_id, run_id):
        """返回当前 Review 的最新结构化章节，不暴露其他 Workflow Output。"""
        await self.detail(actor, project_id, run_id)
        async with self._session_factory() as session:
            return await self._review_repo_factory(
                session
            ).list_latest_section_outputs_scoped(run_id, project_id, actor.owner_id)

    async def artifacts(self, actor, project_id, run_id) -> list[Artifact]:
        await self.detail(actor, project_id, run_id)
        async with self._session_factory() as session:
            return await self._review_repo_factory(session).list_artifacts_scoped(
                run_id, project_id, actor.owner_id
            )

    async def artifact_content(self, actor, project_id, run_id, artifact_id):
        await self.detail(actor, project_id, run_id)
        async with self._session_factory() as session:
            artifact = await self._review_repo_factory(session).get_artifact_scoped(
                artifact_id, run_id, project_id, actor.owner_id
            )
            if artifact is None:
                raise RunNotFoundError(artifact_id)
        content = await self._storage.read(artifact.storage_key)
        if (
            len(content) != artifact.size_bytes
            or hashlib.sha256(content).hexdigest() != artifact.content_hash
        ):
            raise ValueError("artifact_content_integrity_failed")
        return artifact, content


__all__ = ["ReviewOutputType", "ReviewQueryService"]
