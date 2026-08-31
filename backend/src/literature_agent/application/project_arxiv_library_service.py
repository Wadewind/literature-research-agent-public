"""Project 文献库的受限 arXiv 搜索与引入用例。"""

from collections.abc import Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from literature_agent.application.ingestion_service import UploadResult
from literature_agent.application.ports.arxiv_gateway import ArxivGateway
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.arxiv import (
    ArxivError,
    ArxivPaper,
    ArxivSearchQuery,
    parse_versioned_arxiv_id,
)
from literature_agent.domain.exceptions import ProjectArchivedError, ProjectNotFoundError
from literature_agent.domain.paper import PaperTitleSource


class PaperIngestion(Protocol):
    """复用既有上传与 Ingestion 管线所需的最小边界。"""

    async def upload_paper_file(
        self,
        actor: ActorContext,
        project_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        idempotency_key: str,
        correlation_id: str,
        paper_title: str | None = None,
        paper_title_source: PaperTitleSource | None = None,
    ) -> UploadResult: ...


class ProjectArxivLibraryService:
    """在 Project 授权范围内搜索 arXiv，并将选中 PDF 交给 Ingestion。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        project_repo_factory: Callable[[AsyncSession], ProjectRepository],
        arxiv_gateway: ArxivGateway,
        ingestion_service: PaperIngestion,
        max_download_bytes: int,
    ) -> None:
        if max_download_bytes < 1:
            raise ValueError("arxiv_library_download_budget_invalid")
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._arxiv = arxiv_gateway
        self._ingestion = ingestion_service
        self._max_download_bytes = max_download_bytes

    async def search(
        self,
        *,
        actor: ActorContext,
        project_id: str,
        query: ArxivSearchQuery,
    ) -> list[ArxivPaper]:
        """授权后在事务外执行受限 arXiv 检索。"""
        await self._ensure_project_writable(actor, project_id)
        return await self._arxiv.search(query)

    async def import_paper(
        self,
        *,
        actor: ActorContext,
        project_id: str,
        versioned_arxiv_id: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> UploadResult:
        """仅根据已验证 arXiv ID 下载官方 PDF，并复用普通文献导入。"""
        await self._ensure_project_writable(actor, project_id)
        arxiv_id, version = parse_versioned_arxiv_id(versioned_arxiv_id)
        metadata_results = await self._arxiv.search(
            ArxivSearchQuery(expression=f"id:{arxiv_id}", max_results=1)
        )
        metadata = next(
            (paper for paper in metadata_results if paper.arxiv_id == arxiv_id),
            None,
        )
        if metadata is None:
            raise ArxivError(
                "arxiv_paper_not_found",
                temporary=False,
                http_status=404,
            )
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}{version}"
        downloaded = await self._arxiv.download_pdf(
            pdf_url,
            remaining_budget_bytes=self._max_download_bytes,
        )
        safe_id = arxiv_id.replace("/", "-")
        return await self._ingestion.upload_paper_file(
            actor=actor,
            project_id=project_id,
            filename=f"arxiv-{safe_id}{version}.pdf",
            content_type=downloaded.media_type,
            content=downloaded.content,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            paper_title=metadata.title,
            paper_title_source=PaperTitleSource.ARXIV_METADATA,
        )

    async def _ensure_project_writable(self, actor: ActorContext, project_id: str) -> None:
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
        if project is None or project.owner_id != actor.owner_id:
            raise ProjectNotFoundError(project_id)
        if project.is_archived:
            raise ProjectArchivedError(project_id)
