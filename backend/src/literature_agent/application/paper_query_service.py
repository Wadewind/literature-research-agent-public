"""Paper 列表与 PDF 文件查询应用服务（切片 10，供 Web UI 使用）。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime

from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    PaperVersionNotFoundError,
    ProjectNotFoundError,
)
from literature_agent.domain.paper_version import PaperVersion


@dataclass(frozen=True, slots=True)
class PaperVersionSummary:
    """Paper 最新 Version 的列表摘要。"""

    version_id: str
    display_filename: str
    size_bytes: int
    created_at: datetime
    parse_ready: bool


@dataclass(frozen=True, slots=True)
class PaperListItem:
    """Paper 列表条目。"""

    paper_id: str
    created_at: datetime
    latest_version: PaperVersionSummary | None


@dataclass(frozen=True, slots=True)
class VersionFileContent:
    """PDF 文件预览结果：Version 元数据与文件字节。"""

    version: PaperVersion
    content: bytes


class PaperQueryService[TSession: Session]:
    """按授权上下文查询 Paper 列表与 PDF 文件内容。"""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        storage: Storage,
    ) -> None:
        """初始化 PaperQueryService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            project_repo_factory: 根据 session 创建 ProjectRepository 的工厂。
            paper_repo_factory: 根据 session 创建 PaperRepository 的工厂。
            paper_version_repo_factory: 根据 session 创建 PaperVersionRepository 的工厂。
            storage: 文件存储适配器。
        """
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._storage = storage

    async def list_papers(
        self,
        actor: ActorContext,
        project_id: str,
    ) -> list[PaperListItem]:
        """列出 Project 下全部 Paper 及最新 Version 摘要。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            papers = await self._paper_repo_factory(session).list_by_project(project_id)
            version_repo = self._paper_version_repo_factory(session)
            items: list[PaperListItem] = []
            for paper in papers:
                versions = await version_repo.list_by_paper(paper.paper_id)
                latest = versions[0] if versions else None
                items.append(
                    PaperListItem(
                        paper_id=paper.paper_id,
                        created_at=paper.created_at,
                        latest_version=(
                            PaperVersionSummary(
                                version_id=latest.version_id,
                                display_filename=latest.display_filename,
                                size_bytes=latest.size_bytes,
                                created_at=latest.created_at,
                                parse_ready=latest.current_parse_revision_id is not None,
                            )
                            if latest is not None
                            else None
                        ),
                    )
                )
            return items

    async def get_version_file(
        self,
        actor: ActorContext,
        project_id: str,
        version_id: str,
    ) -> VersionFileContent:
        """校验所有权链后返回 PDF 文件字节。

        不要求已有 Parse Revision（上传成功即可预览原文）。
        文件读取发生在数据库事务外。

        异常:
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
            PaperVersionNotFoundError: Version 不存在或不属于该 Project。
            StorageError: 存储中文件缺失或读取失败。
        """
        async with self._session_factory() as session:
            project = await self._project_repo_factory(session).get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)
            version = await self._paper_version_repo_factory(session).get_by_id(version_id)
            if version is None:
                raise PaperVersionNotFoundError(version_id)
            paper = await self._paper_repo_factory(session).get_by_id(version.paper_id)
            if paper is None or paper.project_id != project_id:
                raise PaperVersionNotFoundError(version_id)
        content = await self._storage.read(version.storage_key)
        return VersionFileContent(version=version, content=content)
