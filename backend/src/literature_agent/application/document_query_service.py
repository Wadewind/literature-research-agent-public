"""文档内容查询应用服务（DocumentContentReader 的首版实现）。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime

from literature_agent.application.ports.element_repository import ElementRepository
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.application.ports.parse_revision_repository import (
    ParseRevisionRepository,
)
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
)
from literature_agent.domain.exceptions import (
    DocumentNotReadyError,
    PaperVersionNotFoundError,
    ProjectNotFoundError,
)
from literature_agent.domain.parse_revision import DocumentParseRevision


@dataclass(frozen=True, slots=True)
class SectionInfo:
    """章节概览条目。"""

    section_path: str
    title: str


@dataclass(frozen=True, slots=True)
class DocumentOverview:
    """文档当前 Revision 的概览。"""

    revision_id: str
    parser_name: str
    parser_version: str
    parser_profile_hash: str
    status: str
    completed_at: datetime | None
    element_count: int
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)
    sections: list[SectionInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ElementView:
    """Element 及其来源定位。"""

    element: DocumentElement
    locations: list[ElementSourceLocation]


class DocumentQueryService[TSession: Session]:
    """按授权上下文查询文档结构与 Element 内容。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        parse_revision_repo_factory: Callable[[TSession], ParseRevisionRepository],
        element_repo_factory: Callable[[TSession], ElementRepository],
    ) -> None:
        """初始化 DocumentQueryService。

        参数:
            session_factory: 返回异步上下文管理器的工厂。
            project_repo_factory: 根据 session 创建 ProjectRepository 的工厂。
            paper_repo_factory: 根据 session 创建 PaperRepository 的工厂。
            paper_version_repo_factory: 根据 session 创建 PaperVersionRepository 的工厂。
            parse_revision_repo_factory: 根据 session 创建 ParseRevisionRepository 的工厂。
            element_repo_factory: 根据 session 创建 ElementRepository 的工厂。
        """
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._parse_revision_repo_factory = parse_revision_repo_factory
        self._element_repo_factory = element_repo_factory

    async def get_document(
        self,
        actor: ActorContext,
        project_id: str,
        version_id: str,
    ) -> DocumentOverview:
        """返回文档当前 Revision 概览（章节列表与 Element 统计）。"""
        async with self._session_factory() as session:
            revision = await self._load_current_revision(
                session, actor, project_id, version_id
            )
            element_repo = self._element_repo_factory(session)
            element_count = await element_repo.count_by_revision(revision.revision_id)
            headings = await element_repo.list_by_revision(
                revision.revision_id, element_type="section_heading", limit=500
            )
            sections = [
                SectionInfo(section_path=h.section_path or "", title=h.text or "")
                for h in headings
            ]
            return DocumentOverview(
                revision_id=revision.revision_id,
                parser_name=revision.parser_name,
                parser_version=revision.parser_version,
                parser_profile_hash=revision.parser_profile_hash,
                status=revision.status.value,
                completed_at=revision.completed_at,
                element_count=element_count,
                degraded=revision.degraded,
                warnings=list(revision.warnings),
                sections=sections,
            )

    async def list_elements(
        self,
        actor: ActorContext,
        project_id: str,
        version_id: str,
        *,
        page: int | None = None,
        section_prefix: str | None = None,
        element_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ElementView]:
        """按页码/章节/类型过滤查询当前 Revision 的 Element 及来源定位。"""
        async with self._session_factory() as session:
            revision = await self._load_current_revision(
                session, actor, project_id, version_id
            )
            element_repo = self._element_repo_factory(session)
            elements = await element_repo.list_by_revision(
                revision.revision_id,
                page=page,
                section_prefix=section_prefix,
                element_type=element_type,
                limit=limit,
                offset=offset,
            )
            locations = await element_repo.list_locations(
                [e.element_id for e in elements]
            )
            by_element: dict[str, list[ElementSourceLocation]] = {}
            for loc in locations:
                by_element.setdefault(loc.element_id, []).append(loc)
            return [
                ElementView(element=e, locations=by_element.get(e.element_id, []))
                for e in elements
            ]

    async def _load_current_revision(
        self,
        session: TSession,
        actor: ActorContext,
        project_id: str,
        version_id: str,
    ) -> DocumentParseRevision:
        """校验所有权链并返回当前 Parse Revision。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
            PaperVersionNotFoundError: Version 不存在或不属于该 Project。
            DocumentNotReadyError: 尚无当前 Parse Revision。
        """
        project = await self._project_repo_factory(session).get_by_id(project_id)
        if project is None or project.owner_id != actor.owner_id:
            raise ProjectNotFoundError(project_id)
        version = await self._paper_version_repo_factory(session).get_by_id(version_id)
        if version is None:
            raise PaperVersionNotFoundError(version_id)
        paper = await self._paper_repo_factory(session).get_by_id(version.paper_id)
        if paper is None or paper.project_id != project_id:
            raise PaperVersionNotFoundError(version_id)
        if version.current_parse_revision_id is None:
            raise DocumentNotReadyError(version_id)
        revision = await self._parse_revision_repo_factory(session).get_by_id(
            version.current_parse_revision_id
        )
        if revision is None:
            raise DocumentNotReadyError(version_id)
        return revision
