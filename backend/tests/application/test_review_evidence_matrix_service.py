"""Evidence Matrix 固定提取策略、修复和业务幂等测试。"""

import json
from dataclasses import replace

import pytest

from literature_agent.application.retriever import RetrievalResult
from literature_agent.application.review_evidence_matrix_service import (
    EVIDENCE_MATRIX_JSON_SCHEMA,
    ReviewEvidenceMatrixService,
)
from literature_agent.domain.chunk import Chunk, create_chunk_set
from literature_agent.domain.evidence import create_evidence
from literature_agent.domain.exceptions import (
    EvidenceMatrixInvalidError,
    EvidenceMatrixScopeError,
    RunNotFoundError,
)
from literature_agent.domain.model_types import ChatResult, ModelUsage
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.review import (
    ReviewOutputType,
    ReviewSourceStatus,
    ReviewStage,
    create_review_output,
    create_review_run,
    create_review_source,
)
from literature_agent.domain.review_evidence_matrix import AnalysisDimension
from literature_agent.domain.run import RunStatus, RunType, create_run
from tests.fakes.fake_chunk_repository import FakeChunkRepository
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_evidence_repository import FakeEvidenceRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository

_DIMENSIONS = (
    AnalysisDimension("method", "方法", "使用了什么方法？"),
    AnalysisDimension("limitations", "限制", "有什么限制？"),
    AnalysisDimension("datasets", "数据集", "使用了什么数据集？"),
)


class _Retriever:
    def __init__(self) -> None:
        self.results: list[RetrievalResult] = []
        self.calls: list[dict] = []

    async def retrieve_for_scope(self, **kwargs) -> list[RetrievalResult]:
        self.calls.append(kwargs)
        return list(self.results)


class _Gateway:
    def __init__(self, responses: list[str | None | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[list] = []

    async def generate(self, messages, **_kwargs) -> ChatResult:
        self.calls.append(messages)
        queued = self.responses.pop(0) if self.responses else None
        if isinstance(queued, Exception):
            raise queued
        if queued is not None:
            content = queued
        else:
            request = json.loads(messages[-1].content)
            evidence_id = request["evidence_context"][0]["evidence_id"]
            paper_id = request["paper"]["paper_id"]
            rows = []
            for index, dimension in enumerate(request["dimensions"]):
                rows.append(
                    {
                        "paper_id": paper_id,
                        "dimension_key": dimension["dimension_key"],
                        "status": "extracted" if index == 0 else "insufficient_evidence",
                        "finding": "方法结论" if index == 0 else None,
                        "limitations": None,
                        "evidence_ids": [evidence_id] if index == 0 else [],
                    }
                )
            content = json.dumps({"rows": rows})
        return ChatResult(content, "fake", ModelUsage(10, 5))


class _RunChangingGateway(_Gateway):
    def __init__(self, run_repo: FakeRunRepository) -> None:
        super().__init__()
        self._run_repo = run_repo

    async def generate(self, messages, **kwargs) -> ChatResult:
        result = await super().generate(messages, **kwargs)
        run = await self._run_repo.get_by_id("review-1")
        assert run is not None
        await self._run_repo.add(replace(run, status=RunStatus.SUCCEEDED))
        return result


async def _fixture(*, tokens: list[int] | None = None):
    tokens = tokens or [100, 200]
    run_repo = FakeRunRepository()
    review_repo = FakeReviewRepository()
    version_repo = FakePaperVersionRepository()
    revision_repo = FakeParseRevisionRepository()
    chunk_set_repo = FakeChunkSetRepository(revision_repo)
    chunk_repo = FakeChunkRepository()
    evidence_repo = FakeEvidenceRepository()
    event_repo = FakeEventRepository()
    run = replace(
        create_run("project-1", "user-1", RunType.REVIEW).transition_to(RunStatus.RUNNING),
        run_id="review-1",
    )
    await run_repo.add(run)
    review_repo.authorize_run("review-1", "project-1", "user-1")
    await review_repo.add_review_run(
        create_review_run(
            run_id="review-1",
            research_question="研究问题",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"evidence_extract": "review-evidence-extraction.v1"},
            config_snapshot={
                "source_limit": 10,
                "full_text_token_threshold": 12_000,
                "evidence_context_token_limit": 16_000,
                "retrieval_top_k_per_dimension": 5,
            },
        )
    )
    strategy = create_review_output(
        review_run_id="review-1",
        output_type=ReviewOutputType.SEARCH_STRATEGY,
        output_key="search-strategy",
        version=1,
        schema_version="search-strategy.v1",
        payload={
            "dimensions": [
                {
                    "dimension_key": item.dimension_key,
                    "name": item.name,
                    "extraction_question": item.extraction_question,
                }
                for item in _DIMENSIONS
            ]
        },
        idempotency_key="review-1:search-strategy:v1",
    )
    await review_repo.add_output(strategy)
    version = replace(
        create_paper_version("paper-1", "user-1", "a" * 64, "paper.pdf", 10, "application/pdf"),
        version_id="version-1",
    )
    await version_repo.add(version)
    revision = replace(
        create_parse_revision("version-1", "fake", "1", "profile"),
        revision_id="revision-1",
    ).mark_succeeded(run.created_at)
    await revision_repo.add(revision)
    chunk_set = replace(
        create_chunk_set("revision-1", "chunk-profile"), chunk_set_id="chunk-set-1"
    ).mark_ready(run.created_at)
    await chunk_set_repo.add(chunk_set)
    chunks = [
        Chunk(
            chunk_id=f"chunk-{index}",
            chunk_set_id="chunk-set-1",
            sequence=index,
            text=f"正文 {index}",
            token_count=token_count,
            section_path="Methods",
            page_start=index,
            page_end=index,
        )
        for index, token_count in enumerate(tokens, start=1)
    ]
    await chunk_repo.add_many(chunks)
    source = create_review_source(
        review_run_id="review-1",
        arxiv_id="2401.00001",
        arxiv_version="v1",
        rank=1,
        metadata_snapshot={
            "title": "论文",
            "authors": ["作者"],
            "published_at": "2024-01-01",
            "untrusted_extra": "不得进入模型",
        },
    ).mark_ready("paper-1", "version-1")
    assert source.status is ReviewSourceStatus.READY
    await review_repo.add_source(source)
    return {
        "run_repo": run_repo,
        "review_repo": review_repo,
        "version_repo": version_repo,
        "revision_repo": revision_repo,
        "chunk_set_repo": chunk_set_repo,
        "chunk_repo": chunk_repo,
        "evidence_repo": evidence_repo,
        "event_repo": event_repo,
        "strategy": strategy,
        "chunks": chunks,
    }


def _service(data, retriever: _Retriever, gateway: _Gateway):
    return ReviewEvidenceMatrixService(
        session_factory=fake_session,
        run_repo_factory=lambda _: data["run_repo"],
        review_repo_factory=lambda _: data["review_repo"],
        paper_version_repo_factory=lambda _: data["version_repo"],
        parse_revision_repo_factory=lambda _: data["revision_repo"],
        chunk_set_repo_factory=lambda _: data["chunk_set_repo"],
        chunk_repo_factory=lambda _: data["chunk_repo"],
        evidence_repo_factory=lambda _: data["evidence_repo"],
        event_repo_factory=lambda _: data["event_repo"],
        retriever=retriever,
        model_gateway=gateway,
    )


async def _add_second_source(data) -> None:
    version = replace(
        create_paper_version("paper-2", "user-1", "b" * 64, "paper-2.pdf", 10, "application/pdf"),
        version_id="version-2",
    )
    await data["version_repo"].add(version)
    revision = replace(
        create_parse_revision("version-2", "fake", "1", "profile-2"),
        revision_id="revision-2",
    ).mark_succeeded((await data["run_repo"].get_by_id("review-1")).created_at)
    await data["revision_repo"].add(revision)
    chunk_set = replace(
        create_chunk_set("revision-2", "chunk-profile-2"), chunk_set_id="chunk-set-2"
    ).mark_ready((await data["run_repo"].get_by_id("review-1")).created_at)
    await data["chunk_set_repo"].add(chunk_set)
    await data["chunk_repo"].add_many(
        [
            Chunk(
                chunk_id="chunk-paper-2",
                chunk_set_id="chunk-set-2",
                sequence=1,
                text="第二篇正文",
                token_count=100,
                section_path="Methods",
                page_start=1,
                page_end=1,
            )
        ]
    )
    await data["review_repo"].add_source(
        create_review_source(
            review_run_id="review-1",
            arxiv_id="2401.00002",
            arxiv_version="v1",
            rank=2,
            metadata_snapshot={"title": "第二篇", "authors": ["作者"]},
        ).mark_ready("paper-2", "version-2")
    )


async def test_short_paper_uses_all_ordered_chunks_once_and_replay_reuses_output() -> None:
    data = await _fixture()
    retriever = _Retriever()
    gateway = _Gateway()
    service = _service(data, retriever, gateway)

    first = await service.build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-1",
    )
    second = await service.build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-1",
    )

    assert first.output.output_id == second.output.output_id
    assert len(gateway.calls) == 1
    assert retriever.calls == []
    prompt = json.loads(gateway.calls[0][-1].content)
    assert [item["sequence"] for item in prompt["evidence_context"]] == [1, 2]
    assert "untrusted_extra" not in prompt["paper"]
    assert len(await data["evidence_repo"].list_by_run("review-1")) == 2
    assert len(data["review_repo"].outputs) == 3  # Strategy + 单篇 + 聚合
    assert data["review_repo"].steps[0].status.value == "succeeded"
    events = await data["event_repo"].list_by_run("review-1")
    assert [item.event_type for item in events] == ["evidence_matrix_completed"]
    assert data["review_repo"].review_runs["review-1"].current_stage is ReviewStage.PROPOSE_OUTLINE

    current = data["review_repo"].review_runs["review-1"]
    data["review_repo"].review_runs["review-1"] = replace(
        current, current_stage=ReviewStage.DRAFT_SECTIONS
    )
    await service.build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-2",
    )
    assert data["review_repo"].review_runs["review-1"].current_stage is ReviewStage.DRAFT_SECTIONS


async def test_long_paper_retrieves_each_dimension_and_rejects_wrong_version() -> None:
    data = await _fixture(tokens=[13_000, 1_000])
    retriever = _Retriever()
    chunk = data["chunks"][0]
    retriever.results = [RetrievalResult(chunk, "paper-1", "wrong-version", 1, None, 1.0, 1)]
    service = _service(data, retriever, _Gateway())

    with pytest.raises(EvidenceMatrixScopeError):
        await service.build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )

    assert len(retriever.calls) == 1
    assert retriever.calls[0]["version_scope"] == [("paper-1", "version-1")]


@pytest.mark.parametrize(
    ("schema_version", "dimensions"),
    [
        ("search-strategy.unknown", _DIMENSIONS),
        ("search-strategy.v1", _DIMENSIONS[:2]),
    ],
)
async def test_search_strategy_must_have_fixed_schema_and_three_to_six_dimensions(
    schema_version: str, dimensions: tuple[AnalysisDimension, ...]
) -> None:
    data = await _fixture()
    strategy = data["strategy"]
    data["review_repo"].outputs[0] = replace(
        strategy,
        schema_version=schema_version,
        payload={
            "dimensions": [
                {
                    "dimension_key": item.dimension_key,
                    "name": item.name,
                    "extraction_question": item.extraction_question,
                }
                for item in dimensions
            ]
        },
    )

    with pytest.raises(EvidenceMatrixScopeError):
        await _service(data, _Retriever(), _Gateway()).build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=strategy.output_id,
            correlation_id="corr-1",
        )


async def test_matrix_build_rejects_unknown_model_profile_snapshot() -> None:
    data = await _fixture()
    review = data["review_repo"].review_runs["review-1"]
    data["review_repo"].review_runs["review-1"] = replace(
        review, model_profile_version="review-unknown.v1"
    )

    with pytest.raises(EvidenceMatrixScopeError):
        await _service(data, _Retriever(), _Gateway()).build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.SUCCEEDED])
async def test_matrix_build_rejects_run_outside_running_boundary(status: RunStatus) -> None:
    data = await _fixture()
    run = await data["run_repo"].get_by_id("review-1")
    assert run is not None
    await data["run_repo"].add(replace(run, status=status))

    with pytest.raises(RunNotFoundError):
        await _service(data, _Retriever(), _Gateway()).build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )


async def test_ready_sources_must_not_repeat_same_paper() -> None:
    data = await _fixture()
    await data["review_repo"].add_source(
        create_review_source(
            review_run_id="review-1",
            arxiv_id="2401.00001",
            arxiv_version="v1",
            rank=2,
            metadata_snapshot={"title": "同一论文的重复来源", "authors": ["作者"]},
        ).mark_ready("paper-1", "version-1")
    )

    with pytest.raises(EvidenceMatrixScopeError):
        await _service(data, _Retriever(), _Gateway()).build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )


async def test_matrix_completion_rechecks_running_status_under_run_lock() -> None:
    data = await _fixture()
    gateway = _RunChangingGateway(data["run_repo"])

    with pytest.raises(RunNotFoundError):
        await _service(data, _Retriever(), gateway).build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )

    assert await data["event_repo"].list_by_run("review-1") == []


async def test_invalid_output_gets_exactly_one_repair_then_fails_paper() -> None:
    data = await _fixture()
    gateway = _Gateway(["not-json", "still-not-json"])
    service = _service(data, _Retriever(), gateway)

    with pytest.raises(EvidenceMatrixInvalidError):
        await service.build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )

    assert len(gateway.calls) == 2
    repair = json.loads(gateway.calls[1][-1].content.split("\n", 1)[1])
    assert repair["validation_errors"][0]["code"] == "invalid_json"
    assert gateway.calls[1][:2] == gateway.calls[0]
    assert gateway.calls[1][2].role == "assistant"
    assert len(data["review_repo"].outputs) == 2
    assert data["review_repo"].outputs[1].payload == {
        "status": "failed",
        "source_id": data["review_repo"].sources[0].source_id,
        "paper_id": "paper-1",
        "error_code": "evidence_matrix_invalid",
    }
    assert data["review_repo"].steps[0].status.value == "failed"


async def test_existing_evidence_same_chunk_with_different_scope_is_rejected() -> None:
    data = await _fixture()
    await data["evidence_repo"].add_many(
        [
            create_evidence(
                run_id="review-1",
                project_id="project-1",
                paper_id="other-paper",
                version_id="version-1",
                parse_revision_id="revision-1",
                chunk_id="chunk-1",
                section_path="Methods",
                page_start=1,
                page_end=1,
                excerpt="正文 1",
            )
        ]
    )
    gateway = _Gateway()

    with pytest.raises(EvidenceMatrixScopeError):
        await _service(data, _Retriever(), gateway).build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )

    assert gateway.calls == []


async def test_partial_failure_final_output_crash_replay_skips_failed_paper_model() -> None:
    data = await _fixture()
    await _add_second_source(data)
    gateway = _Gateway(["not-json", "still-not-json", None])
    service = _service(data, _Retriever(), gateway)

    first = await service.build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-1",
    )
    call_count = len(gateway.calls)
    second = await service.build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-1",
    )

    assert (first.valid_papers, first.failed_papers) == (1, 1)
    assert second.output.output_id == first.output.output_id
    assert len(gateway.calls) == call_count == 3
    assert len(await data["event_repo"].list_by_run("review-1")) == 1


async def test_paper_failure_replay_survives_later_transient_error() -> None:
    data = await _fixture()
    await _add_second_source(data)
    gateway = _Gateway(
        ["not-json", "still-not-json", TimeoutError("provider timeout"), None]
    )
    service = _service(data, _Retriever(), gateway)

    with pytest.raises(TimeoutError, match="provider timeout"):
        await service.build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )

    result = await service.build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-1",
    )

    invoked_papers = [json.loads(call[1].content)["paper"]["paper_id"] for call in gateway.calls]
    assert invoked_papers == ["paper-1", "paper-1", "paper-2", "paper-2"]
    assert (result.valid_papers, result.failed_papers) == (1, 1)
    assert result.output.payload["paper_failures"] == [
        {
            "source_id": data["review_repo"].sources[0].source_id,
            "paper_id": "paper-1",
            "error_code": "evidence_matrix_invalid",
        }
    ]


async def test_final_output_replay_rejects_conflicting_identity() -> None:
    data = await _fixture()
    gateway = _Gateway()
    service = _service(data, _Retriever(), gateway)
    result = await service.build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-1",
    )
    output_index = data["review_repo"].outputs.index(result.output)
    data["review_repo"].outputs[output_index] = replace(
        result.output,
        output_key="paper:conflicting-source",
    )

    with pytest.raises(EvidenceMatrixScopeError, match="身份或版本"):
        await service.build(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            correlation_id="corr-1",
        )


async def test_long_paper_retrieves_all_dimensions_then_orders_and_deduplicates() -> None:
    data = await _fixture(tokens=[13_000, 1_000])
    retriever = _Retriever()
    retriever.results = [
        RetrievalResult(data["chunks"][1], "paper-1", "version-1", 1, None, 1.0, 1),
        RetrievalResult(data["chunks"][0], "paper-1", "version-1", 2, None, 0.5, 2),
    ]
    gateway = _Gateway()

    await _service(data, retriever, gateway).build(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        correlation_id="corr-1",
    )

    assert len(retriever.calls) == 3
    prompt = json.loads(gateway.calls[0][-1].content)
    assert [item["sequence"] for item in prompt["evidence_context"]] == [1, 2]
    item_schema = EVIDENCE_MATRIX_JSON_SCHEMA["properties"]["rows"]["items"]
    assert set(item_schema["properties"]) == set(item_schema["required"])
