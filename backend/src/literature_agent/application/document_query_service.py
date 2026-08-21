"""文档内容查询应用服务（DocumentContentReader 的首版实现）。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime

from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.element_repository import ElementRepository
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.application.ports.parse_revision_repository import (
    ParseRevisionRepository,
)
from literature_agent.application.ports.project_paper_repository import (
    ProjectPaperRepository,
)
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.run_repository import RunRepository
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


@dataclass(frozen=True, slots=True)
class ChunkSetStatusView:
    """当前 Revision 最新 ChunkSet 的索引状态。"""

    chunk_set_id: str
    status: str
    chunk_count: int
    embedded_count: int
    profile_hash: str


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """文档当前 Revision 的索引状态（index-status API 的返回体）。"""

    revision_id: str
    chunk_set: ChunkSetStatusView | None
    indexing_run_id: str | None


class DocumentQueryService[TSession: Session]:
    """按授权上下文查询文档结构与 Element 内容。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        project_paper_repo_factory: Callable[[TSession], ProjectPaperRepository],
        parse_revision_repo_factory: Callable[[TSession], ParseRevisionRepository],
        element_repo_factory: Callable[[TSession], ElementRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
        chunk_repo_factory: Callable[[TSession], ChunkRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
    ) -> None:
        """初始化 DocumentQueryService。

        参数:
            session_factory: 返回异步上下文管理器的工厂。
            project_repo_factory: 根据 session 创建 ProjectRepository 的工厂。
            paper_repo_factory: 根据 session 创建 PaperRepository 的工厂。
            paper_version_repo_factory: 根据 session 创建 PaperVersionRepository 的工厂。
            parse_revision_repo_factory: 根据 session 创建 ParseRevisionRepository 的工厂。
            element_repo_factory: 根据 session 创建 ElementRepository 的工厂。
            chunk_set_repo_factory: 根据 session 创建 ChunkSetRepository 的工厂。
            chunk_repo_factory: 根据 session 创建 ChunkRepository 的工厂。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
        """
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._project_paper_repo_factory = project_paper_repo_factory
        self._parse_revision_repo_factory = parse_revision_repo_factory
        self._element_repo_factory = element_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory
        self._chunk_repo_factory = chunk_repo_factory
        self._run_repo_factory = run_repo_factory

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

    async def get_index_status(
        self,
        actor: ActorContext,
        project_id: str,
        version_id: str,
    ) -> IndexStatus:
        """返回当前 Revision 的索引状态（最新 ChunkSet 与最近一次 indexing Run）。

        授权链与 ``get_document`` 一致；无 ChunkSet 时 ``chunk_set`` 为 None
        （例如 indexing Run 尚未创建产物或刚启动）。
        """
        async with self._session_factory() as session:
            revision = await self._load_current_revision(
                session, actor, project_id, version_id
            )
            chunk_set = await self._chunk_set_repo_factory(
                session
            ).get_latest_by_revision(revision.revision_id)
            indexing_run_id = await self._run_repo_factory(
                session
            ).get_latest_indexing_run_id(revision.revision_id)
            if chunk_set is None:
                return IndexStatus(
                    revision_id=revision.revision_id,
                    chunk_set=None,
                    indexing_run_id=indexing_run_id,
                )
            chunk_repo = self._chunk_repo_factory(session)
            return IndexStatus(
                revision_id=revision.revision_id,
                chunk_set=ChunkSetStatusView(
                    chunk_set_id=chunk_set.chunk_set_id,
                    status=chunk_set.status.value,
                    chunk_count=await chunk_repo.count_by_chunk_set(
                        chunk_set.chunk_set_id
                    ),
                    embedded_count=await chunk_repo.count_embedded(
                        chunk_set.chunk_set_id
                    ),
                    profile_hash=chunk_set.profile_hash,
                ),
                indexing_run_id=indexing_run_id,
            )

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
        relation = await self._project_paper_repo_factory(session).get_by_version(
            project_id, version_id
        )
        paper = await self._paper_repo_factory(session).get_by_id(version.paper_id)
        if (
            relation is None
            or relation.paper_id != version.paper_id
            or paper is None
            or paper.owner_id != actor.owner_id
        ):
            raise PaperVersionNotFoundError(version_id)
        if version.current_parse_revision_id is None:
            raise DocumentNotReadyError(version_id)
        revision = await self._parse_revision_repo_factory(session).get_by_id(
            version.current_parse_revision_id
        )
        if revision is None:
            raise DocumentNotReadyError(version_id)
        return revision
