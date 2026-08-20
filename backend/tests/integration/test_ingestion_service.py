"""IngestionService 与 PostgreSQL 的事务编排集成测试。"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.ingestion_service import IngestionService
from literature_agent.domain.actor import ActorContext
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from tests.fakes.fake_storage import FakeStorage


async def test_new_file_upload_commits_complete_graph(db_engine, project: str) -> None:
    """新文件上传应按外键顺序原子提交 Paper、Run、Version 和关系。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    storage = FakeStorage()
    service = IngestionService(
        max_upload_size_bytes=1024 * 1024,
        session_factory=session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        storage=storage,
    )

    result = await service.upload_paper_file(
        actor=ActorContext(owner_id="user-1"),
        project_id=project,
        filename="new-paper.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.4\nnew integration document\n",
        idempotency_key="integration-new-upload",
        correlation_id="integration-correlation",
    )

    assert result.run_id is not None
    assert result.status == "queued"
    assert result.reused is False

    async with session_factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(result.run_id)
        version = await SqlalchemyPaperVersionRepository(session).get_by_id(
            result.version_id
        )
        relation = await SqlalchemyProjectPaperRepository(session).get(
            project, result.paper_id
        )
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(result.run_id)

    assert run is not None
    assert version is not None
    assert version.ingestion_run_id == result.run_id
    assert relation is not None
    assert relation.selected_version_id == result.version_id
    assert outbox is not None
