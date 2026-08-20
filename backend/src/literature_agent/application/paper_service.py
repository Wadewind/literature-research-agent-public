"""Paper 归档/恢复应用服务。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import PaperNotFoundError
from literature_agent.domain.paper import Paper

TSession = TypeVar("TSession", bound=Session)


class PaperService:
    """owner 个人文献库 Paper 的写操作用例层。

    Paper 归档只冻结新增收录等写操作，不影响已有 ProjectPaper
    收录关系和历史数据。
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        paper_repo_factory: Callable[[TSession], PaperRepository],
    ) -> None:
        """初始化 PaperService。

        参数:
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            paper_repo_factory: 根据 session 创建 PaperRepository 的工厂。
        """
        self._session_factory = session_factory
        self._paper_repo_factory = paper_repo_factory

    async def archive_paper(self, actor: ActorContext, paper_id: str) -> Paper:
        """归档 Paper；幂等，已归档时直接返回。

        异常:
            PaperNotFoundError: Paper 不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            repo = self._paper_repo_factory(session)
            paper = await repo.get_by_id(paper_id)
            if paper is None or paper.owner_id != actor.owner_id:
                raise PaperNotFoundError(paper_id)
            if paper.is_archived:
                return paper
            archived = paper.archive()
            await repo.update(archived)
            await session.commit()
            return archived

    async def restore_paper(self, actor: ActorContext, paper_id: str) -> Paper:
        """恢复已归档 Paper；幂等，未归档时直接返回。

        异常:
            PaperNotFoundError: Paper 不存在或不属于当前 actor。
        """
        async with self._session_factory() as session:
            repo = self._paper_repo_factory(session)
            paper = await repo.get_by_id(paper_id)
            if paper is None or paper.owner_id != actor.owner_id:
                raise PaperNotFoundError(paper_id)
            if not paper.is_archived:
                return paper
            restored = paper.restore()
            await repo.update(restored)
            await session.commit()
            return restored
