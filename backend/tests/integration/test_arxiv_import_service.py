"""arXiv 项目导入的 PostgreSQL 幂等与事务集成测试。"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.arxiv_import_service import ArxivProjectImportService
from literature_agent.application.ports.arxiv_gateway import ArxivGateway
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.arxiv import ArxivPaper, ArxivSearchQuery, DownloadedPdf
from literature_agent.domain.review import create_review_run
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.models import (
    EventORM,
    PaperORM,
    PaperVersionORM,
    ProjectPaperORM,
    QueueOutboxORM,
    ReviewSourceORM,
    RunDependencyORM,
    RunORM,
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
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from tests.fakes.fake_storage import FakeStorage


class Gateway(ArxivGateway):
    def __init__(self, paper: ArxivPaper, pdf: DownloadedPdf) -> None:
        self.paper = paper
        self.pdf = pdf

    async def search(self, query: ArxivSearchQuery) -> list[ArxivPaper]:
        return [self.paper]

    async def download_pdf(
        self, url: str, *, remaining_budget_bytes: int
    ) -> DownloadedPdf:
        assert len(self.pdf.content) <= remaining_budget_bytes
        return self.pdf


def _paper() -> ArxivPaper:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    return ArxivPaper(
        arxiv_id="2401.00001",
        arxiv_version="v1",
        title="Reliable Agents",
        abstract="Abstract",
        authors=("Alice",),
        categories=("cs.AI",),
        published_at=now,
        updated_at=now,
        pdf_url="https://arxiv.org/pdf/2401.00001v1",
    )


def _service(factory, gateway, storage, review_factory=SqlalchemyReviewRepository):
    return ArxivProjectImportService(
        session_factory=factory,
        arxiv_gateway=gateway,
        storage=storage,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        review_repo_factory=review_factory,
        total_download_budget_bytes=1024,
    )


async def _seed_review(factory, project_id: str) -> str:
    async with factory() as session:
        run = create_run(project_id, "user-1", RunType.REVIEW)
        await SqlalchemyRunRepository(session).add(run)
        await session.flush()
        await SqlalchemyReviewRepository(session).add_review_run(
            create_review_run(
                run_id=run.run_id,
                research_question="Reliable agents",
                workflow_version="review.v1",
                model_profile_version="review-default.v1",
                prompt_versions={"search": "search_strategy.v1"},
                config_snapshot={"max_sources": 1},
            )
        )
        await session.commit()
        return run.run_id


@pytest.mark.asyncio
async def test_concurrent_review_runs_converge_on_one_ingestion_bundle(
    db_engine, project: str
) -> None:
    """不同父 Run 同 hash 首次导入由 advisory lock 收敛，而非暴露 IntegrityError。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    first_run, second_run = await asyncio.gather(
        _seed_review(factory, project), _seed_review(factory, project)
    )
    paper = _paper()
    gateway = Gateway(
        paper, DownloadedPdf.from_content(b"%PDF-shared", "application/pdf")
    )
    service = _service(factory, gateway, FakeStorage())
    for run_id in (first_run, second_run):
        await service.search_sources(
            actor=ActorContext(owner_id="user-1"),
            project_id=project,
            review_run_id=run_id,
            query=ArxivSearchQuery("all:agents", max_results=1),
            correlation_id="corr-search",
        )

    results = await asyncio.gather(
        *(
            service.import_sources(
                actor=ActorContext(owner_id="user-1"),
                project_id=project,
                review_run_id=run_id,
                correlation_id="corr-import",
            )
            for run_id in (first_run, second_run)
        )
    )

    assert [result.imported for result in results] == [1, 1]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(PaperORM)) == 1
        assert await session.scalar(select(func.count()).select_from(PaperVersionORM)) == 1
        assert await session.scalar(select(func.count()).select_from(ProjectPaperORM)) == 1
        ingestion_count = await session.scalar(
            select(func.count())
            .select_from(RunORM)
            .where(RunORM.run_type == RunType.INGESTION.value)
        )
        assert ingestion_count == 1
        assert await session.scalar(select(func.count()).select_from(QueueOutboxORM)) == 1
        assert await session.scalar(select(func.count()).select_from(ReviewSourceORM)) == 2
        assert await session.scalar(select(func.count()).select_from(RunDependencyORM)) == 4


@pytest.mark.asyncio
async def test_database_failure_rolls_back_bundle_but_keeps_reconcilable_cache(
    db_engine, project: str
) -> None:
    """缓存先写，登记失败则数据库整体回滚；内容寻址对象不做危险补偿删除。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run_id = await _seed_review(factory, project)
    paper = _paper()
    gateway = Gateway(
        paper, DownloadedPdf.from_content(b"%PDF-rollback", "application/pdf")
    )
    storage = FakeStorage()
    normal = _service(factory, gateway, storage)
    await normal.search_sources(
        actor=ActorContext(owner_id="user-1"),
        project_id=project,
        review_run_id=run_id,
        query=ArxivSearchQuery("all:agents"),
        correlation_id="corr-search",
    )

    class FailingReviewRepository(SqlalchemyReviewRepository):
        async def save_source(self, source) -> None:
            await super().save_source(source)
            raise RuntimeError("injected-db-failure")

    failing = _service(factory, gateway, storage, FailingReviewRepository)
    with pytest.raises(RuntimeError, match="injected-db-failure"):
        await failing.import_sources(
            actor=ActorContext(owner_id="user-1"),
            project_id=project,
            review_run_id=run_id,
            correlation_id="corr-import",
        )

    assert len(storage._objects) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(PaperORM)) == 0
        assert await session.scalar(select(func.count()).select_from(PaperVersionORM)) == 0
        assert await session.scalar(select(func.count()).select_from(ProjectPaperORM)) == 0
        assert await session.scalar(select(func.count()).select_from(QueueOutboxORM)) == 0
        ingestion_count = await session.scalar(
            select(func.count())
            .select_from(RunORM)
            .where(RunORM.run_type == RunType.INGESTION.value)
        )
        assert ingestion_count == 0
        run_created_count = await session.scalar(
            select(func.count())
            .select_from(EventORM)
            .where(EventORM.event_type == "run_created")
        )
        assert run_created_count == 0
        assert await session.scalar(select(func.count()).select_from(RunDependencyORM)) == 0
        source = await session.scalar(select(ReviewSourceORM))
        assert source is not None and source.status == "discovered"
