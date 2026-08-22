"""最终综述 Artifact 服务的幂等、取消与完整映射测试。"""

from dataclasses import replace

import pytest

from literature_agent.application.review_export_service import ReviewExportService
from literature_agent.domain.evidence import (
    AnswerStatus,
    Citation,
    create_claim,
    create_claim_set,
    create_evidence,
)
from literature_agent.domain.exceptions import RunNotFoundError
from literature_agent.domain.model_invocation import (
    InvocationStatus,
    ModelCapability,
    create_model_invocation,
)
from literature_agent.domain.review import (
    ReviewOutputType,
    ReviewStage,
    ReviewStepKey,
    create_review_output,
    create_review_run,
    create_review_source,
    create_run_step,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from tests.fakes.fake_claim_set_repository import FakeClaimSetRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_evidence_repository import FakeEvidenceRepository
from tests.fakes.fake_model_invocation_repository import FakeModelInvocationRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository
from tests.fakes.fake_storage import FakeStorage


async def _fixture():
    run_repo = FakeRunRepository()
    review_repo = FakeReviewRepository()
    evidence_repo = FakeEvidenceRepository()
    claim_repo = FakeClaimSetRepository()
    event_repo = FakeEventRepository()
    invocation_repo = FakeModelInvocationRepository()
    storage = FakeStorage()
    run = replace(
        create_run("project-1", "user-1", RunType.REVIEW),
        run_id="review-1",
        status=RunStatus.RUNNING,
        event_sequence=10,
    )
    await run_repo.add(run)
    review_repo.authorize_run("review-1", "project-1", "user-1")
    review = replace(
        create_review_run(
            run_id="review-1",
            research_question="什么方法更可靠？",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"section_draft": "section_draft.v1"},
            config_snapshot={"source_limit": 10},
        ),
        current_stage=ReviewStage.EXPORT_REVIEW,
    )
    await review_repo.add_review_run(review)
    strategy = create_review_output(
        review_run_id="review-1",
        output_type=ReviewOutputType.SEARCH_STRATEGY,
        output_key="search-strategy",
        version=1,
        schema_version="search-strategy.v1",
        payload={
            "normalized_question": "什么方法更可靠？",
            "arxiv_query": "all:reliability",
            "dimensions": [
                {
                    "dimension_key": key,
                    "name": key,
                    "extraction_question": f"如何分析 {key}？",
                }
                for key in ("method", "evaluation", "limitations")
            ],
        },
        idempotency_key="strategy",
    )
    matrix = create_review_output(
        review_run_id="review-1",
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="evidence-matrix",
        version=1,
        schema_version="evidence-matrix.v1",
        payload={"rows": [], "paper_failures": [], "summary": {}},
        idempotency_key="matrix",
    )
    section = create_review_output(
        review_run_id="review-1",
        output_type=ReviewOutputType.SECTION,
        output_key="section:methods",
        version=1,
        schema_version="section.v1",
        payload={
            "section_key": "methods",
            "title": "方法",
            "status": "answered",
            "summary": "比较摘要",
            "claims": [{"text": "方法 A 更稳定。", "evidence_ids": ["evidence-1"]}],
            "terminology": [],
        },
        idempotency_key="section",
    )
    consistency = create_review_output(
        review_run_id="review-1",
        output_type=ReviewOutputType.CONSISTENCY_REPORT,
        output_key="consistency-report",
        version=1,
        schema_version="consistency-report.v1",
        payload={"status": "consistent", "issues": []},
        idempotency_key="consistency",
    )
    for item in (strategy, matrix, section, consistency):
        await review_repo.add_output(item)
    step = (
        create_run_step(
            run_id="review-1",
            step_key=ReviewStepKey.CONSISTENCY_CHECK,
            sequence=12,
            idempotency_key="consistency-step",
            input_refs={
                "outline_output_id": "unused-outline-id",
                "evidence_matrix_output_id": matrix.output_id,
                "section_output_ids": [section.output_id],
                "claim_set_id": "cs-1",
                "prompt_version": "consistency_check.v1",
                "schema_version": "consistency-report.v1",
            },
        )
        .start()
        .succeed({"consistency_output_id": consistency.output_id})
    )
    await review_repo.add_step(step)
    source = create_review_source(
        review_run_id="review-1",
        arxiv_id="2401.00001",
        arxiv_version="v1",
        rank=1,
        metadata_snapshot={
            "title": "论文一",
            "authors": ["作者一"],
            "published_at": "2024-01-01",
        },
    ).mark_ready("paper-1", "version-1")
    await review_repo.add_source(source)
    evidence = replace(
        create_evidence(
            run_id="review-1",
            project_id="project-1",
            paper_id="paper-1",
            version_id="version-1",
            parse_revision_id="revision-1",
            chunk_id="chunk-1",
            section_path="Methods",
            page_start=2,
            page_end=2,
            excerpt="证据",
        ),
        evidence_id="evidence-1",
    )
    await evidence_repo.add_many([evidence])
    claim_set = replace(create_claim_set("review-1", AnswerStatus.ANSWERED), claim_set_id="cs-1")
    claim = replace(create_claim("cs-1", 1, "方法 A 更稳定。"), claim_id="claim-1")
    await claim_repo.add_claim_set(claim_set)
    await claim_repo.add_claims([claim])
    await claim_repo.add_citations([Citation("claim-1", "evidence-1")])
    await invocation_repo.add(
        create_model_invocation(
            run_id="review-1",
            capability=ModelCapability.CHAT,
            provider="fake",
            model="fake-chat",
            status=InvocationStatus.SUCCEEDED,
            latency_ms=1,
            prompt_tokens=12,
            completion_tokens=3,
        )
    )
    service = ReviewExportService(
        session_factory=fake_session,
        run_repo_factory=lambda _: run_repo,
        review_repo_factory=lambda _: review_repo,
        evidence_repo_factory=lambda _: evidence_repo,
        claim_set_repo_factory=lambda _: claim_repo,
        event_repo_factory=lambda _: event_repo,
        model_invocation_repo_factory=lambda _: invocation_repo,
        storage=storage,
    )
    return locals()


async def test_export_writes_six_artifacts_once_and_finalize_succeeds() -> None:
    data = await _fixture()
    kwargs = {
        "run_id": "review-1",
        "project_id": "project-1",
        "owner_id": "user-1",
        "approved_outline_output_id": "unused-outline-id",
        "evidence_matrix_output_id": data["matrix"].output_id,
        "section_output_ids": [data["section"].output_id],
        "claim_set_id": "cs-1",
        "consistency_output_id": data["consistency"].output_id,
        "correlation_id": "corr-1",
    }
    # 当前指针是导出闭包的一部分。
    review = data["review_repo"].review_runs["review-1"]
    data["review_repo"].review_runs["review-1"] = replace(
        review, current_outline_output_id="unused-outline-id"
    )

    first = await data["service"].export(**kwargs)
    second = await data["service"].export(**kwargs)

    assert first.final_output.output_id == second.final_output.output_id
    assert len(first.artifacts) == len(second.artifacts) == 6
    assert first.final_output.payload["reference_count"] == 1
    assert "reference_mapping" not in first.final_output.payload
    assert len(data["review_repo"].artifacts) == 6
    assert len(data["storage"]._objects) == 6
    assert data["review_repo"].review_runs["review-1"].statistics_summary == {
        "source_discovered": 1,
        "source_ready": 1,
        "source_failed": 0,
        "model_invocations": 1,
        "prompt_tokens": 12,
        "completion_tokens": 3,
    }
    markdown = await data["storage"].read(first.markdown_artifact.storage_key)
    assert "方法 A 更稳定。[1]" in markdown.decode()
    assert len(await data["event_repo"].list_by_run("review-1")) == 1

    await data["service"].finalize(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        final_artifact_id=first.markdown_artifact.artifact_id,
        correlation_id="corr-1",
    )
    assert (await data["run_repo"].get_by_id("review-1")).status is RunStatus.SUCCEEDED
    assert [event.event_type for event in await data["event_repo"].list_by_run("review-1")] == [
        "review_artifact_created",
        "run_succeeded",
    ]


async def test_cancel_after_storage_write_commits_no_artifact_or_event() -> None:
    data = await _fixture()
    review = data["review_repo"].review_runs["review-1"]
    data["review_repo"].review_runs["review-1"] = replace(
        review, current_outline_output_id="unused-outline-id"
    )
    original_write = data["storage"].write

    async def cancel_on_write(key: str, content: bytes) -> None:
        await original_write(key, content)
        run = await data["run_repo"].get_by_id("review-1")
        await data["run_repo"].add(replace(run, status=RunStatus.CANCEL_REQUESTED))

    data["storage"].write = cancel_on_write
    with pytest.raises(RunNotFoundError):
        await data["service"].export(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            approved_outline_output_id="unused-outline-id",
            evidence_matrix_output_id=data["matrix"].output_id,
            section_output_ids=[data["section"].output_id],
            claim_set_id="cs-1",
            consistency_output_id=data["consistency"].output_id,
            correlation_id="corr-1",
        )
    assert data["review_repo"].artifacts == []
    assert await data["event_repo"].list_by_run("review-1") == []


async def test_crash_after_storage_write_reuses_cache_and_converges_on_retry() -> None:
    data = await _fixture()
    review = data["review_repo"].review_runs["review-1"]
    data["review_repo"].review_runs["review-1"] = replace(
        review, current_outline_output_id="unused-outline-id"
    )
    original = data["review_repo"].get_or_add_artifact
    failed_once = False

    async def crash_once(artifact):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("模拟 Storage 完成后数据库提交前崩溃")
        return await original(artifact)

    data["review_repo"].get_or_add_artifact = crash_once
    kwargs = {
        "run_id": "review-1",
        "project_id": "project-1",
        "owner_id": "user-1",
        "approved_outline_output_id": "unused-outline-id",
        "evidence_matrix_output_id": data["matrix"].output_id,
        "section_output_ids": [data["section"].output_id],
        "claim_set_id": "cs-1",
        "consistency_output_id": data["consistency"].output_id,
        "correlation_id": "corr-1",
    }

    with pytest.raises(RuntimeError, match="提交前崩溃"):
        await data["service"].export(**kwargs)
    assert len(data["storage"]._objects) == 6
    assert data["review_repo"].artifacts == []
    assert await data["event_repo"].list_by_run("review-1") == []

    result = await data["service"].export(**kwargs)
    assert len(result.artifacts) == 6
    assert len(data["storage"]._objects) == 6
    assert len(data["review_repo"].artifacts) == 6
    assert [event.event_type for event in await data["event_repo"].list_by_run("review-1")] == [
        "review_artifact_created"
    ]
