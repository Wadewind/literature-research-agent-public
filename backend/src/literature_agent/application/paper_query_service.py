"""个人文献库、Project 收录与 PDF 文件查询服务。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime

from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.application.ports.project_paper_repository import (
    ProjectPaperRepository,
)
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    PaperVersionNotFoundError,
    ProjectNotFoundError,
)
from literature_agent.domain.paper import Paper
from literature_agent.domain.paper_version import PaperVersion
from literature_agent.domain.project_paper import ProjectPaper


@dataclass(frozen=True, slots=True)
class PaperVersionSummary:
    """文献列表需要的固定 Version 摘要。"""

    version_id: str
    display_filename: str
    size_bytes: int
    created_at: datetime
    parse_ready: bool
    ingestion_run_id: str | None


@dataclass(frozen=True, slots=True)
class PaperListItem:
    """个人文献库或 Project 文献列表条目。"""

    paper_id: str
    created_at: datetime
    version: PaperVersionSummary
    project_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VersionFileContent:
    """PDF 文件预览结果：Version 元数据与文件字节。"""

    version: PaperVersion
    content: bytes


class PaperQueryService[TSession: Session]:
    """按 owner 与 Project 收录关系查询 Paper。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        project_paper_repo_factory: Callable[[TSession], ProjectPaperRepository],
        storage: Storage,
    ) -> None:
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._project_paper_repo_factory = project_paper_repo_factory
        self._storage = storage

    async def list_project_papers(
        self,
        actor: ActorContext,
        project_id: str,
    ) -> list[PaperListItem]:
        """列出 Project 收录的 Paper，并返回关系固定的 Version。"""
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            relation_repo = self._project_paper_repo_factory(session)
            relations = await relation_repo.list_by_project(project_id)
            paper_repo = self._paper_repo_factory(session)
            version_repo = self._paper_version_repo_factory(session)
            items: list[PaperListItem] = []
            for relation in relations:
                paper = await paper_repo.get_by_id(relation.paper_id)
                version = await version_repo.get_by_id(relation.selected_version_id)
                if paper is None or version is None or paper.owner_id != actor.owner_id:
                    continue
                memberships = await relation_repo.list_by_paper(paper.paper_id)
                items.append(self._item(paper, version, memberships))
            return items

    async def list_library_papers(self, actor: ActorContext) -> list[PaperListItem]:
        """列出 owner 个人文献库及各 Paper 的 Project 收录范围。"""
        async with self._session_factory() as session:
            papers = await self._paper_repo_factory(session).list_by_owner(actor.owner_id)
            version_repo = self._paper_version_repo_factory(session)
            relation_repo = self._project_paper_repo_factory(session)
            items: list[PaperListItem] = []
            for paper in papers:
                versions = await version_repo.list_by_paper(paper.paper_id)
                if not versions:
                    continue
                memberships = await relation_repo.list_by_paper(paper.paper_id)
                items.append(self._item(paper, versions[0], memberships))
            return items

    async def get_version_file(
        self,
        actor: ActorContext,
        project_id: str,
        version_id: str,
    ) -> VersionFileContent:
        """校验 Project 固定 Version 的收录关系后读取 PDF。"""
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            relation = await self._project_paper_repo_factory(session).get_by_version(
                project_id, version_id
            )
            if relation is None:
                raise PaperVersionNotFoundError(version_id)
            version = await self._paper_version_repo_factory(session).get_by_id(version_id)
            paper = (
                await self._paper_repo_factory(session).get_by_id(relation.paper_id)
                if version is not None
                else None
            )
            if version is None or paper is None or paper.owner_id != actor.owner_id:
                raise PaperVersionNotFoundError(version_id)
        content = await self._storage.read(version.storage_key)
        return VersionFileContent(version=version, content=content)

    @staticmethod
    def _item(
        paper: Paper,
        version: PaperVersion,
        memberships: list[ProjectPaper],
    ) -> PaperListItem:
        return PaperListItem(
            paper_id=paper.paper_id,
            created_at=paper.created_at,
            version=PaperVersionSummary(
                version_id=version.version_id,
                display_filename=version.display_filename,
                size_bytes=version.size_bytes,
                created_at=version.created_at,
                parse_ready=version.current_parse_revision_id is not None,
                ingestion_run_id=version.ingestion_run_id,
            ),
            project_ids=tuple(relation.project_id for relation in memberships),
        )
