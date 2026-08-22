"""Review Workflow 数据契约领域测试。"""

from dataclasses import replace

import pytest

from literature_agent.domain.review import (
    ArtifactType,
    HumanInputAction,
    HumanInputRequestStatus,
    ReviewDependencyStatus,
    ReviewDependencyType,
    ReviewOutputType,
    ReviewSourceStatus,
    ReviewStepKey,
    ReviewStepStatus,
    create_artifact,
    create_human_input,
    create_human_input_request,
    create_review_dependency,
    create_review_output,
    create_review_run,
    create_review_source,
    create_run_step,
)


def test_create_review_contract_entities() -> None:
    """各创建函数生成稳定 ID、受限状态和最小可追溯字段。"""
    review = create_review_run(
        run_id="run-1",
        research_question="如何可靠恢复 HITL 工作流？",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={"outline": "outline_generate.v1"},
        config_snapshot={"source_limit": 10},
    )
    step = create_run_step(
        run_id=review.run_id,
        step_key=ReviewStepKey.VALIDATE_REQUEST,
        sequence=1,
        idempotency_key="run-1:validate_request:1",
    )
    source = create_review_source(
        review_run_id=review.run_id,
        arxiv_id="2401.00001",
        arxiv_version="v2",
        rank=1,
        metadata_snapshot={"title": "Durable HITL"},
    )
    dependency = create_review_dependency(
        parent_run_id=review.run_id,
        dependency_type=ReviewDependencyType.PAPER_VERSION,
        target_paper_version_id="version-1",
    )
    output = create_review_output(
        review_run_id=review.run_id,
        output_type=ReviewOutputType.OUTLINE,
        output_key="outline",
        version=1,
        schema_version="outline.v1",
        payload={"sections": []},
        idempotency_key="run-1:outline:1",
    )
    request = create_human_input_request(
        review_run_id=review.run_id,
        request_version=1,
        outline_output_id=output.output_id,
        allowed_actions=[HumanInputAction.APPROVE, HumanInputAction.EDIT],
    )
    human_input = create_human_input(
        request=request,
        action=HumanInputAction.APPROVE,
        payload={"outline_output_id": output.output_id},
        submitted_by="user-1",
        idempotency_key="outline-submit-1",
    )
    artifact = create_artifact(
        review_run_id=review.run_id,
        project_id="project-1",
        owner_id="user-1",
        artifact_type=ArtifactType.REVIEW_MARKDOWN,
        storage_key="user-1/reviews/run-1/review.md",
        content_hash="a" * 64,
        size_bytes=128,
        media_type="text/markdown",
        idempotency_key="run-1:review-markdown",
        source_output_id=output.output_id,
        metadata={"citation_style": "numeric"},
    )

    assert review.run_id == "run-1"
    assert review.statistics_summary == {
        "source_discovered": 0,
        "source_ready": 0,
        "source_failed": 0,
        "model_invocations": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    assert step.status is ReviewStepStatus.PENDING
    assert source.status is ReviewSourceStatus.DISCOVERED
    assert dependency.status is ReviewDependencyStatus.PENDING
    assert request.status is HumanInputRequestStatus.OPEN
    assert human_input.request_version == request.request_version
    assert artifact.source_output_id == output.output_id


def test_review_run_rejects_invalid_version_name() -> None:
    """Workflow/Profile/Prompt 版本必须使用稳定的 name.vN 格式。"""
    with pytest.raises(ValueError, match="name.vN"):
        create_review_run(
            run_id="run-1",
            research_question="如何可靠恢复 HITL 工作流？",
            workflow_version="review-latest",
            model_profile_version="review-default.v1",
            prompt_versions={"outline": "outline_generate.v1"},
            config_snapshot={"source_limit": 10},
        )


@pytest.mark.parametrize(
    ("dependency_type", "targets"),
    [
        (ReviewDependencyType.RUN, {}),
        (
            ReviewDependencyType.RUN,
            {"target_run_id": "run-2", "target_paper_version_id": "version-1"},
        ),
        (
            ReviewDependencyType.CHUNK_SET,
            {"target_paper_version_id": "version-1"},
        ),
    ],
)
def test_dependency_requires_exact_matching_target(
    dependency_type: ReviewDependencyType,
    targets: dict[str, str],
) -> None:
    """依赖类型必须且只能对应一个同名目标。"""
    with pytest.raises(ValueError, match="依赖目标"):
        create_review_dependency(
            parent_run_id="run-1",
            dependency_type=dependency_type,
            **targets,
        )


def test_human_input_request_can_only_resolve_once() -> None:
    """同一请求只能从 OPEN 解决一次，动作必须在允许集合内。"""
    request = create_human_input_request(
        review_run_id="run-1",
        request_version=1,
        outline_output_id="output-1",
        allowed_actions=[HumanInputAction.APPROVE],
    )
    human_input = create_human_input(
        request=request,
        action=HumanInputAction.APPROVE,
        payload={},
        submitted_by="user-1",
        idempotency_key="submit-1",
    )
    resolved = request.resolve(human_input.human_input_id)

    assert resolved.status is HumanInputRequestStatus.RESOLVED
    assert resolved.resolved_input_id == human_input.human_input_id
    with pytest.raises(ValueError, match="已经解决"):
        resolved.resolve("input-2")
    with pytest.raises(ValueError, match="不允许"):
        create_human_input(
            request=request,
            action=HumanInputAction.FEEDBACK,
            payload={},
            submitted_by="user-1",
            idempotency_key="submit-2",
        )


def test_review_output_is_versioned_and_payload_is_bounded() -> None:
    """Output 版本从 1 开始，受控 JSON 不能承载大型正文。"""
    with pytest.raises(ValueError, match="版本"):
        create_review_output(
            review_run_id="run-1",
            output_type=ReviewOutputType.OUTLINE,
            output_key="outline",
            version=0,
            schema_version="outline.v1",
            payload={},
            idempotency_key="outline-0",
        )
    with pytest.raises(ValueError, match="过大"):
        create_review_output(
            review_run_id="run-1",
            output_type=ReviewOutputType.SECTION,
            output_key="methods",
            version=1,
            schema_version="section.v1",
            payload={"markdown": "x" * 300_000},
            idempotency_key="section-1",
        )


def test_artifact_rejects_inline_content_and_invalid_hash() -> None:
    """Artifact 只保存 Storage 引用；哈希和大小必须合法。"""
    with pytest.raises(ValueError, match="哈希"):
        create_artifact(
            review_run_id="run-1",
            project_id="project-1",
            owner_id="user-1",
            artifact_type=ArtifactType.REVIEW_MARKDOWN,
            storage_key="reviews/run-1/review.md",
            content_hash="bad",
            size_bytes=1,
            media_type="text/markdown",
            idempotency_key="artifact-1",
        )

    request = create_human_input_request(
        review_run_id="run-1",
        request_version=1,
        outline_output_id="output-1",
        allowed_actions=[HumanInputAction.APPROVE],
    )
    closed = replace(request, status=HumanInputRequestStatus.CANCELLED)
    with pytest.raises(ValueError, match="开放"):
        create_human_input(
            request=closed,
            action=HumanInputAction.APPROVE,
            payload={},
            submitted_by="user-1",
            idempotency_key="submit-closed",
        )
