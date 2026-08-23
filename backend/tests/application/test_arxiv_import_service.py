from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from literature_agent.application.arxiv_import_service import ArxivProjectImportService
from literature_agent.application.ports.arxiv_gateway import ArxivGateway
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.arxiv import (
    ArxivError,
    ArxivPaper,
    ArxivSearchQuery,
    DownloadedPdf,
)
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.exceptions import ProjectNotFoundError
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.review import (
    ReviewDependencyStatus,
    ReviewDependencyType,
    ReviewSourceStatus,
    ReviewStage,
    ReviewStepKey,
    create_review_run,
)
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.fake_arxiv import FixtureArxivGateway
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository
from tests.fakes.fake_storage import FakeStorage


class FakeArxivGateway(ArxivGateway):
    def __init__(self, papers: list[ArxivPaper]) -> None:
        self.papers = papers
        self.downloads: dict[str, DownloadedPdf | ArxivError] = {}
        self.download_calls: list[tuple[str, int]] = []
        self.search_calls = 0

    async def search(self, query: ArxivSearchQuery) -> list[ArxivPaper]:
        self.search_calls += 1
        return self.papers[: query.max_results]

    async def download_pdf(
        self, url: str, *, remaining_budget_bytes: int
    ) -> DownloadedPdf:
        self.download_calls.append((url, remaining_budget_bytes))
        result = self.downloads[url]
        if isinstance(result, ArxivError):
            raise result
        if len(result.content) > remaining_budget_bytes:
            raise ArxivError("arxiv_total_download_budget_exceeded", temporary=False)
        return result


class TransactionAwareStorage(FakeStorage):
    def __init__(self, state: dict[str, bool]) -> None:
        super().__init__()
        self._state = state

    async def write(self, key: str, content: bytes) -> None:
        assert self._state["in_transaction"] is False
        await super().write(key, content)


def _paper(identifier: str, *, rank: int = 1) -> ArxivPaper:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    return ArxivPaper(
        arxiv_id=identifier,
        arxiv_version="v1",
        title=f"Paper {rank}",
        abstract="Abstract",
        authors=("Alice",),
        categories=("cs.AI",),
        published_at=now,
        updated_at=now,
        pdf_url=f"https://arxiv.org/pdf/{identifier}v1",
    )


async def _fixture(
    papers: list[ArxivPaper],
    *,
    budget: int = 100,
    gateway_override: ArxivGateway | None = None,
):
    state = {"in_transaction": False}

    class Session:
        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    @asynccontextmanager
    async def session_factory():
        assert state["in_transaction"] is False
        state["in_transaction"] = True
        try:
            yield Session()
        finally:
            state["in_transaction"] = False

    projects = FakeProjectRepository()
    paper_repo = FakePaperRepository()
    version_repo = FakePaperVersionRepository()
    relations = FakeProjectPaperRepository()
    revisions = FakeParseRevisionRepository()
    chunks = FakeChunkSetRepository(revisions)
    runs = FakeRunRepository()
    events = FakeEventRepository()
    outboxes = FakeOutboxRepository()
    reviews = FakeReviewRepository()
    gateway = gateway_override or FakeArxivGateway(papers)
    storage = TransactionAwareStorage(state)
    project = create_project("owner-1", "Review", "")
    await projects.add(project)
    run = create_run(project.project_id, "owner-1", RunType.REVIEW)
    run = replace(run, event_sequence=2)
    await runs.add(run)
    review = create_review_run(
        run_id=run.run_id,
        research_question="Agent reliability",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={"search": "search_strategy.v1"},
        config_snapshot={"max_sources": 10},
    )
    await reviews.add_review_run(review)
    reviews.authorize_run(run.run_id, project.project_id, "owner-1")
    service = ArxivProjectImportService(
        session_factory=session_factory,
        arxiv_gateway=gateway,
        storage=storage,
        project_repo_factory=lambda _: projects,
        paper_repo_factory=lambda _: paper_repo,
        paper_version_repo_factory=lambda _: version_repo,
        project_paper_repo_factory=lambda _: relations,
        chunk_set_repo_factory=lambda _: chunks,
        run_repo_factory=lambda _: runs,
        event_repo_factory=lambda _: events,
        outbox_repo_factory=lambda _: outboxes,
        review_repo_factory=lambda _: reviews,
        total_download_budget_bytes=budget,
    )
    return locals()


@pytest.mark.asyncio
async def test_service_rejects_nonpositive_total_download_budget() -> None:
    with pytest.raises(ValueError, match="arxiv_total_download_budget_invalid"):
        await _fixture([], budget=0)


@pytest.mark.asyncio
async def test_search_persists_deterministic_sources_and_replays() -> None:
    ctx = await _fixture([_paper("2401.00002", rank=1), _paper("2401.00001", rank=2)])
    kwargs = {
        "actor": ActorContext(owner_id="owner-1"),
        "project_id": ctx["project"].project_id,
        "review_run_id": ctx["run"].run_id,
        "query": ArxivSearchQuery("all:agents", max_results=2),
        "correlation_id": "corr-1",
    }

    first = await ctx["service"].search_sources(**kwargs)
    replay = await ctx["service"].search_sources(**kwargs)

    assert [(source.rank, source.arxiv_id) for source in first] == [
        (1, "2401.00002"),
        (2, "2401.00001"),
    ]
    assert [source.source_id for source in replay] == [source.source_id for source in first]
    assert ctx["gateway"].search_calls == 1
    events = await ctx["events"].list_by_run(ctx["run"].run_id)
    assert [event.event_type for event in events] == ["arxiv_search_completed"]
    assert (
        ctx["reviews"].review_runs[ctx["run"].run_id].current_stage
        is ReviewStage.IMPORT_ARXIV_PAPERS
    )


@pytest.mark.asyncio
async def test_empty_search_result_has_persisted_completion_fact() -> None:
    ctx = await _fixture([])
    kwargs = {
        "actor": ActorContext(owner_id="owner-1"),
        "project_id": ctx["project"].project_id,
        "review_run_id": ctx["run"].run_id,
        "query": ArxivSearchQuery("all:no-match"),
        "correlation_id": "corr-1",
    }
    assert await ctx["service"].search_sources(**kwargs) == []
    assert await ctx["service"].search_sources(**kwargs) == []
    assert ctx["gateway"].search_calls == 1
    assert len(ctx["reviews"].steps) == 1


@pytest.mark.asyncio
async def test_changed_search_query_conflicts_before_second_external_call() -> None:
    ctx = await _fixture([_paper("2401.00001")])
    common = {
        "actor": ActorContext(owner_id="owner-1"),
        "project_id": ctx["project"].project_id,
        "review_run_id": ctx["run"].run_id,
        "correlation_id": "corr-1",
    }
    await ctx["service"].search_sources(
        **common, query=ArxivSearchQuery("all:agents")
    )
    with pytest.raises(ValueError, match="arxiv_search_replay_conflict"):
        await ctx["service"].search_sources(
            **common, query=ArxivSearchQuery("all:different")
        )
    assert ctx["gateway"].search_calls == 1


@pytest.mark.asyncio
async def test_import_registers_ingestion_bundle_dependencies_and_is_idempotent() -> None:
    paper = _paper("2401.00001")
    ctx = await _fixture([paper])
    downloaded = DownloadedPdf.from_content(b"%PDF-document", "application/pdf")
    ctx["gateway"].downloads[paper.pdf_url] = downloaded
    await ctx["service"].search_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        query=ArxivSearchQuery("all:agents"),
        correlation_id="corr-1",
    )

    first = await ctx["service"].import_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        correlation_id="corr-1",
    )
    replay = await ctx["service"].import_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        correlation_id="corr-2",
    )

    sources = ctx["reviews"].sources
    assert first.imported == replay.imported == 1
    assert sources[0].status is ReviewSourceStatus.IMPORTING
    assert sources[0].paper_id and sources[0].paper_version_id
    assert len(await ctx["paper_repo"].list_by_owner("owner-1")) == 1
    assert len(await ctx["version_repo"].list_by_paper(sources[0].paper_id)) == 1
    assert len(await ctx["relations"].list_by_project(ctx["project"].project_id)) == 1
    ingestion_runs = [
        value
        for value in ctx["runs"]._runs.values()
        if value.run_type == RunType.INGESTION.value
    ]
    assert len(ingestion_runs) == 1
    assert await ctx["outboxes"].get_by_run_id(ingestion_runs[0].run_id) is not None
    assert {item.dependency_type for item in ctx["reviews"].dependencies} == {
        ReviewDependencyType.RUN,
        ReviewDependencyType.PAPER_VERSION,
    }
    assert len(ctx["gateway"].download_calls) == 1
    assert [item.step_key for item in ctx["reviews"].steps][-1] is ReviewStepKey.IMPORT_ARXIV_PAPERS
    assert [item.event_type for item in await ctx["events"].list_by_run(ctx["run"].run_id)][
        -1
    ] == "review_source_import_completed"
    assert next(iter(ctx["storage"]._objects)).startswith(
        "owner-1/arxiv-cache/sha256/"
    )


@pytest.mark.asyncio
async def test_partial_download_failure_is_stable_and_next_source_continues() -> None:
    first, second = _paper("2401.00001", rank=1), _paper("2401.00002", rank=2)
    ctx = await _fixture([first, second])
    ctx["gateway"].downloads[first.pdf_url] = ArxivError(
        "arxiv_pdf_not_found", temporary=False
    )
    ctx["gateway"].downloads[second.pdf_url] = DownloadedPdf.from_content(
        b"%PDF-second", "application/pdf"
    )
    await ctx["service"].search_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        query=ArxivSearchQuery("all:agents"),
        correlation_id="corr-1",
    )

    summary = await ctx["service"].import_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        correlation_id="corr-1",
    )

    assert (summary.imported, summary.failed) == (1, 1)
    assert [source.status for source in ctx["reviews"].sources] == [
        ReviewSourceStatus.FAILED,
        ReviewSourceStatus.IMPORTING,
    ]
    assert ctx["reviews"].sources[0].failure_code == "arxiv_pdf_not_found"


@pytest.mark.asyncio
async def test_versioned_demo_fixture_imports_three_and_keeps_one_stable_failure() -> None:
    """生产 Fake arXiv 与真实导入服务组成可重复的部分失败离线闭环。"""
    gateway = FixtureArxivGateway()
    ctx = await _fixture([], budget=100_000, gateway_override=gateway)
    scope = {
        "actor": ActorContext(owner_id="owner-1"),
        "project_id": ctx["project"].project_id,
        "review_run_id": ctx["run"].run_id,
        "correlation_id": "demo-fixture",
    }
    await ctx["service"].search_sources(
        **scope,
        query=ArxivSearchQuery("all:agent", max_results=4),
    )

    first = await ctx["service"].import_sources(**scope)
    replay = await ctx["service"].import_sources(**scope)

    assert (first.imported, first.failed) == (3, 1)
    assert replay == first
    assert [source.status for source in ctx["reviews"].sources] == [
        ReviewSourceStatus.IMPORTING,
        ReviewSourceStatus.IMPORTING,
        ReviewSourceStatus.IMPORTING,
        ReviewSourceStatus.FAILED,
    ]
    assert ctx["reviews"].sources[-1].failure_code == "fake_arxiv_pdf_unavailable"
    ingestion_runs = [
        run for run in ctx["runs"]._runs.values() if run.run_type == RunType.INGESTION.value
    ]
    assert len(ingestion_runs) == 3


@pytest.mark.asyncio
async def test_temporary_download_failure_remains_retryable() -> None:
    paper = _paper("2401.00001")
    ctx = await _fixture([paper])
    ctx["gateway"].downloads[paper.pdf_url] = ArxivError(
        "arxiv_pdf_timeout", temporary=True
    )
    await ctx["service"].search_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        query=ArxivSearchQuery("all:agents"),
        correlation_id="corr-1",
    )
    with pytest.raises(ArxivError, match="arxiv_pdf_timeout"):
        await ctx["service"].import_sources(
            actor=ActorContext(owner_id="owner-1"),
            project_id=ctx["project"].project_id,
            review_run_id=ctx["run"].run_id,
            correlation_id="corr-1",
        )
    assert ctx["reviews"].sources[0].status is ReviewSourceStatus.DISCOVERED


@pytest.mark.asyncio
async def test_existing_ready_chunk_set_is_reused_with_verified_version_dependency() -> None:
    arxiv_paper = _paper("2401.00001")
    ctx = await _fixture([arxiv_paper])
    downloaded = DownloadedPdf.from_content(b"%PDF-ready", "application/pdf")
    ctx["gateway"].downloads[arxiv_paper.pdf_url] = downloaded
    paper = create_paper("owner-1")
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="owner-1",
        file_hash=downloaded.content_hash,
        storage_key="owner-1/existing.pdf",
        size_bytes=len(downloaded.content),
        content_type="application/pdf",
    )
    revision = create_parse_revision(
        version.version_id, "fake", "1", "a" * 64
    ).mark_succeeded(datetime.now(UTC))
    chunk_set = create_chunk_set(revision.revision_id, "b" * 64).mark_ready(
        datetime.now(UTC)
    )
    await ctx["paper_repo"].add(paper)
    await ctx["version_repo"].add(version)
    await ctx["revisions"].add(revision)
    await ctx["chunks"].add(chunk_set)
    await ctx["service"].search_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        query=ArxivSearchQuery("all:agents"),
        correlation_id="corr-1",
    )

    summary = await ctx["service"].import_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        correlation_id="corr-1",
    )

    assert summary.ready == 1
    assert (
        ctx["reviews"].review_runs[ctx["run"].run_id].current_stage
        is ReviewStage.BUILD_EVIDENCE_MATRIX
    )
    source = ctx["reviews"].sources[0]
    assert source.status is ReviewSourceStatus.READY
    assert source.paper_version_id == version.version_id
    dependencies = ctx["reviews"].dependencies
    assert {
        (
            value.dependency_type,
            value.status,
            value.target_paper_version_id,
            value.target_chunk_set_id,
        )
        for value in dependencies
    } == {
        (
            ReviewDependencyType.PAPER_VERSION,
            ReviewDependencyStatus.SATISFIED,
            version.version_id,
            None,
        ),
        (
            ReviewDependencyType.CHUNK_SET,
            ReviewDependencyStatus.SATISFIED,
            None,
            chunk_set.chunk_set_id,
        ),
    }


@pytest.mark.asyncio
async def test_reused_ingestion_run_from_other_project_is_not_a_run_dependency() -> None:
    arxiv_paper = _paper("2401.00001")
    ctx = await _fixture([arxiv_paper])
    downloaded = DownloadedPdf.from_content(b"%PDF-cross-project", "application/pdf")
    ctx["gateway"].downloads[arxiv_paper.pdf_url] = downloaded
    paper = create_paper("owner-1")
    other_project = create_project("owner-1", "Other", "")
    ingestion_run = create_run(other_project.project_id, "owner-1", RunType.INGESTION)
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="owner-1",
        file_hash=downloaded.content_hash,
        storage_key="owner-1/existing.pdf",
        size_bytes=len(downloaded.content),
        content_type="application/pdf",
        ingestion_run_id=ingestion_run.run_id,
    )
    await ctx["paper_repo"].add(paper)
    await ctx["version_repo"].add(version)
    await ctx["runs"].add(ingestion_run)
    await ctx["service"].search_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        query=ArxivSearchQuery("all:agents"),
        correlation_id="corr-1",
    )
    await ctx["service"].import_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        correlation_id="corr-1",
    )
    assert [value.dependency_type for value in ctx["reviews"].dependencies] == [
        ReviewDependencyType.PAPER_VERSION
    ]


@pytest.mark.asyncio
async def test_archived_reused_paper_fails_instead_of_becoming_invisible_source() -> None:
    arxiv_paper = _paper("2401.00001")
    ctx = await _fixture([arxiv_paper])
    downloaded = DownloadedPdf.from_content(b"%PDF-archived", "application/pdf")
    ctx["gateway"].downloads[arxiv_paper.pdf_url] = downloaded
    paper = create_paper("owner-1").archive()
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="owner-1",
        file_hash=downloaded.content_hash,
        storage_key="owner-1/existing.pdf",
        size_bytes=len(downloaded.content),
        content_type="application/pdf",
    )
    await ctx["paper_repo"].add(paper)
    await ctx["version_repo"].add(version)
    await ctx["service"].search_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        query=ArxivSearchQuery("all:agents"),
        correlation_id="corr-1",
    )
    summary = await ctx["service"].import_sources(
        actor=ActorContext(owner_id="owner-1"),
        project_id=ctx["project"].project_id,
        review_run_id=ctx["run"].run_id,
        correlation_id="corr-1",
    )
    assert summary.failed == 1
    assert ctx["reviews"].sources[0].failure_code == "review_source_paper_archived"
    assert await ctx["relations"].list_by_project(ctx["project"].project_id) == []


@pytest.mark.asyncio
async def test_cross_owner_scope_is_rejected_before_download() -> None:
    paper = _paper("2401.00001")
    ctx = await _fixture([paper])
    with pytest.raises(ProjectNotFoundError):
        await ctx["service"].import_sources(
            actor=ActorContext(owner_id="attacker"),
            project_id=ctx["project"].project_id,
            review_run_id=ctx["run"].run_id,
            correlation_id="corr-1",
        )
    assert ctx["gateway"].download_calls == []


@pytest.mark.asyncio
async def test_cross_owner_search_is_rejected_before_external_io() -> None:
    paper = _paper("2401.00001")
    ctx = await _fixture([paper])
    with pytest.raises(ProjectNotFoundError):
        await ctx["service"].search_sources(
            actor=ActorContext(owner_id="attacker"),
            project_id=ctx["project"].project_id,
            review_run_id=ctx["run"].run_id,
            query=ArxivSearchQuery("all:agents"),
            correlation_id="corr-1",
        )
    assert ctx["gateway"].search_calls == 0
