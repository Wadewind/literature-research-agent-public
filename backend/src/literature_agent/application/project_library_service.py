"""个人文献库与 Project 收录关系的应用服务。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TypeVar

from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.application.ports.project_paper_repository import (
    ProjectPaperRepository,
)
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    PaperNotFoundError,
    PaperVersionNotFoundError,
    ProjectNotFoundError,
)
from literature_agent.domain.project_paper import ProjectPaper, create_project_paper

TSession = TypeVar("TSession", bound=Session)


@dataclass(frozen=True, slots=True)
class AddPaperResult:
    """添加已有 Paper 的结果。"""

    relation: ProjectPaper
    already_added: bool


class ProjectLibraryService:
    """添加和移除 Project 的个人文献库收录。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        project_paper_repo_factory: Callable[[TSession], ProjectPaperRepository],
    ) -> None:
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._project_paper_repo_factory = project_paper_repo_factory

    async def add_existing_paper(
        self,
        actor: ActorContext,
        project_id: str,
        paper_id: str,
        version_id: str,
    ) -> AddPaperResult:
        """把 owner 文献库中的指定 PaperVersion 收录到 Project。"""
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            paper = await self._paper_repo_factory(session).get_by_id(paper_id)
            if paper is None or paper.owner_id != actor.owner_id:
                raise PaperNotFoundError(paper_id)
            version = await self._paper_version_repo_factory(session).get_by_id(version_id)
            if version is None or version.paper_id != paper_id:
                raise PaperVersionNotFoundError(version_id)
            relation_repo = self._project_paper_repo_factory(session)
            existing = await relation_repo.get(project_id, paper_id)
            if existing is not None:
                return AddPaperResult(existing, already_added=True)
            relation = create_project_paper(project_id, paper_id, version_id)
            await relation_repo.add(relation)
            await session.commit()
            return AddPaperResult(relation, already_added=False)

    async def remove_paper(
        self,
        actor: ActorContext,
        project_id: str,
        paper_id: str,
    ) -> bool:
        """从 Project 移除 Paper，但保留个人文献库内容。"""
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            removed = await self._project_paper_repo_factory(session).remove(
                project_id, paper_id
            )
            await session.commit()
            return removed
