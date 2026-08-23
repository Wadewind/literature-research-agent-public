"""Phase 3 Review Workflow 数据契约 PostgreSQL 集成测试。"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.review_workflow_service import ReviewWorkflowService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.project import create_project
from literature_agent.domain.queue_outbox import OutboxStatus
from literature_agent.domain.review import (
    ArtifactType,
    HumanInputAction,
    ReviewDependencyType,
    ReviewOutputType,
    ReviewStage,
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
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.models import RunORM
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
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


async def _seed_review(session, project_id: str):
    """持久化通用 Review Run 与扩展记录。"""
    run = create_run(project_id, "user-1", RunType.REVIEW, {})
    await SqlalchemyRunRepository(session).add(run)
    await session.flush()
    review = create_review_run(
        run_id=run.run_id,
        research_question="可靠 HITL",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={"outline": "outline_generate.v1"},
        config_snapshot={"source_limit": 10},
    )
    await SqlalchemyReviewRepository(session).add_review_run(review)
    await session.flush()
    return run, review


async def test_review_repository_roundtrip_and_scope(session, project: str) -> None:
    """所有子资源可往返，并同时受 owner 与 Project 范围过滤。"""
    run, review = await _seed_review(session, project)
    target_run = create_run(project, "user-1", RunType.INGESTION, {})
    await SqlalchemyRunRepository(session).add(target_run)
    await session.flush()
    repo = SqlalchemyReviewRepository(session)

    step = create_run_step(
        run_id=run.run_id,
        step_key=ReviewStepKey.VALIDATE_REQUEST,
        sequence=1,
        idempotency_key="validate:1",
    )
    source = create_review_source(
        review_run_id=run.run_id,
        arxiv_id="2401.00001",
        arxiv_version="v1",
        rank=1,
        metadata_snapshot={"title": "Durable Workflow"},
    )
    dependency = create_review_dependency(
        parent_run_id=run.run_id,
        dependency_type=ReviewDependencyType.RUN,
        target_run_id=target_run.run_id,
    )
    output_v1 = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.OUTLINE,
        output_key="outline",
        version=1,
        schema_version="outline.v1",
        payload={"sections": []},
        idempotency_key="outline:1",
    )
    output_v2 = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.OUTLINE,
        output_key="outline",
        version=2,
        schema_version="outline.v1",
        payload={"sections": [{"section_key": "methods"}]},
        idempotency_key="outline:2",
    )
    request = create_human_input_request(
        review_run_id=run.run_id,
        request_version=1,
        outline_output_id=output_v2.output_id,
        allowed_actions=list(HumanInputAction),
    )
    human_input = create_human_input(
        request=request,
        action=HumanInputAction.APPROVE,
        payload={"outline_output_id": output_v2.output_id},
        submitted_by="user-1",
        idempotency_key="submit:1",
    )
    artifact = create_artifact(
        review_run_id=run.run_id,
        project_id=project,
        owner_id="user-1",
        artifact_type=ArtifactType.REVIEW_MARKDOWN,
        storage_key=f"user-1/reviews/{run.run_id}/review.md",
        content_hash="a" * 64,
        size_bytes=100,
        media_type="text/markdown",
        idempotency_key="artifact:review",
        source_output_id=output_v2.output_id,
        metadata={"citation_style": "numeric"},
    )
    await repo.add_step(step)
    await repo.add_source(source)
    await repo.add_dependency(dependency)
    await repo.add_output(output_v1)
    await repo.add_output(output_v2)
    await session.flush()
    await repo.add_human_input_request(request)
    await session.flush()
    await repo.add_human_input(human_input)
    await repo.add_artifact(artifact)
    await session.commit()

    assert await repo.get_review_run_scoped(run.run_id, project, "user-1") == review
    assert await repo.get_review_run_scoped(run.run_id, project, "user-2") is None
    assert await repo.get_review_run_scoped(run.run_id, str(uuid4()), "user-1") is None
    assert await repo.list_steps_scoped(run.run_id, project, "user-1") == [step]
    assert await repo.list_sources_scoped(run.run_id, project, "user-1") == [source]
    assert await repo.list_dependencies_scoped(run.run_id, project, "user-1") == [dependency]
    assert await repo.list_outputs_scoped(run.run_id, project, "user-1") == [
        output_v1,
        output_v2,
    ]
    assert await repo.get_open_human_input_request_scoped(run.run_id, project, "user-1") == request
    assert await repo.list_artifacts_scoped(run.run_id, project, "user-1") == [artifact]
    assert await repo.list_artifacts_scoped(run.run_id, project, "user-2") == []


async def test_review_list_is_stably_sorted_and_scoped(session, project: str) -> None:
    """列表只返回当前 owner/Project 的 Review，并稳定按新到旧排序。"""
    first_run, first_review = await _seed_review(session, project)
    second_run, second_review = await _seed_review(session, project)
    other_project = create_project("user-1", "Other", "")
    other_owner_project = create_project("user-2", "Private", "")
    await SqlalchemyProjectRepository(session).add(other_project)
    await SqlalchemyProjectRepository(session).add(other_owner_project)
    await session.flush()
    await _seed_review(session, other_project.project_id)
    hidden_run = create_run(other_owner_project.project_id, "user-2", RunType.REVIEW, {})
    await SqlalchemyRunRepository(session).add(hidden_run)
    await session.flush()
    await SqlalchemyReviewRepository(session).add_review_run(
        create_review_run(
            run_id=hidden_run.run_id,
            research_question="private",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"outline": "outline_generate.v1"},
            config_snapshot={"source_limit": 10},
        )
    )
    wrong_type_run = create_run(project, "user-1", RunType.INGESTION, {})
    await SqlalchemyRunRepository(session).add(wrong_type_run)
    await session.flush()
    await SqlalchemyReviewRepository(session).add_review_run(
        create_review_run(
            run_id=wrong_type_run.run_id,
            research_question="not a review",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"outline": "outline_generate.v1"},
            config_snapshot={"source_limit": 10},
        )
    )
    await session.commit()

    rows = await SqlalchemyReviewRepository(session).list_review_runs_scoped(
        project, "user-1"
    )

    assert rows == [(second_run, second_review), (first_run, first_review)]
    assert await SqlalchemyReviewRepository(session).list_review_runs_scoped(
        project, "user-2"
    ) == []


async def test_latest_sections_are_filtered_versioned_and_scoped(session, project: str) -> None:
    """章节读模型只返回每个 key 的最新版本，并隐藏其他 Output 与越权范围。"""
    run, _ = await _seed_review(session, project)
    repo = SqlalchemyReviewRepository(session)
    outputs = [
        create_review_output(
            review_run_id=run.run_id,
            output_type=output_type,
            output_key=output_key,
            version=version,
            schema_version=(
                "section.v1" if output_type is ReviewOutputType.SECTION else "outline.v1"
            ),
            payload={"section_key": output_key.removeprefix("section:"), "version": version},
            idempotency_key=f"{output_key}:{version}",
        )
        for output_type, output_key, version in (
            (ReviewOutputType.SECTION, "section:results", 1),
            (ReviewOutputType.OUTLINE, "outline", 1),
            (ReviewOutputType.SECTION, "section:methods", 1),
            (ReviewOutputType.SECTION, "section:methods", 2),
        )
    ]
    for output in outputs:
        await repo.add_output(output)
    await session.commit()

    rows = await repo.list_latest_section_outputs_scoped(run.run_id, project, "user-1")

    assert [(item.output_key, item.version) for item in rows] == [
        ("section:methods", 2),
        ("section:results", 1),
    ]
    assert await repo.list_latest_section_outputs_scoped(run.run_id, project, "user-2") == []


async def test_database_constraints_prevent_duplicate_effects(session, project: str) -> None:
    """Output/Dependency/HumanInput 的唯一约束兜底至少一次执行。"""
    run, _ = await _seed_review(session, project)
    target = create_run(project, "user-1", RunType.INGESTION, {})
    await SqlalchemyRunRepository(session).add(target)
    await session.flush()
    repo = SqlalchemyReviewRepository(session)

    dependency = create_review_dependency(
        parent_run_id=run.run_id,
        dependency_type=ReviewDependencyType.RUN,
        target_run_id=target.run_id,
    )
    await repo.add_dependency(dependency)
    await repo.add_dependency(
        create_review_dependency(
            parent_run_id=run.run_id,
            dependency_type=ReviewDependencyType.RUN,
            target_run_id=target.run_id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    # 前一事务整体回滚，重新建立 Output/Request 链测试同一请求只能提交一次。
    run, _ = await _seed_review(session, project)
    repo = SqlalchemyReviewRepository(session)
    output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.OUTLINE,
        output_key="outline",
        version=1,
        schema_version="outline.v1",
        payload={"sections": []},
        idempotency_key="outline:1",
    )
    await repo.add_output(output)
    await session.flush()
    request = create_human_input_request(
        review_run_id=run.run_id,
        request_version=1,
        outline_output_id=output.output_id,
        allowed_actions=[HumanInputAction.APPROVE],
    )
    await repo.add_human_input_request(request)
    await session.flush()
    await repo.add_human_input(
        create_human_input(
            request=request,
            action=HumanInputAction.APPROVE,
            payload={},
            submitted_by="user-1",
            idempotency_key="submit:1",
        )
    )
    await repo.add_human_input(
        create_human_input(
            request=request,
            action=HumanInputAction.APPROVE,
            payload={},
            submitted_by="user-1",
            idempotency_key="submit:2",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_step_and_source_effectively_once_constraints(session, project: str) -> None:
    """Step 的顺序/幂等键与 Source 的版本/rank 均由数据库拒绝重复。"""
    run, _ = await _seed_review(session, project)
    repo = SqlalchemyReviewRepository(session)
    await repo.add_step(
        create_run_step(
            run_id=run.run_id,
            step_key=ReviewStepKey.VALIDATE_REQUEST,
            sequence=1,
            idempotency_key="step:validate:1",
        )
    )
    await repo.add_source(
        create_review_source(
            review_run_id=run.run_id,
            arxiv_id="2401.00001",
            arxiv_version="v1",
            rank=1,
            metadata_snapshot={},
        )
    )
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_step(
                create_run_step(
                    run_id=run.run_id,
                    step_key=ReviewStepKey.SEARCH_ARXIV,
                    sequence=1,
                    idempotency_key="step:search:1",
                )
            )
            await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_step(
                create_run_step(
                    run_id=run.run_id,
                    step_key=ReviewStepKey.SEARCH_ARXIV,
                    sequence=2,
                    idempotency_key="step:validate:1",
                )
            )
            await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_source(
                create_review_source(
                    review_run_id=run.run_id,
                    arxiv_id="2401.00001",
                    arxiv_version="v1",
                    rank=2,
                    metadata_snapshot={},
                )
            )
            await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_source(
                create_review_source(
                    review_run_id=run.run_id,
                    arxiv_id="2401.00002",
                    arxiv_version="v1",
                    rank=1,
                    metadata_snapshot={},
                )
            )
            await session.flush()


async def test_output_and_open_request_effectively_once_constraints(
    session, project: str
) -> None:
    """Output 的版本/幂等键与同 Run 单 open Request 由数据库兜底。"""
    run, _ = await _seed_review(session, project)
    repo = SqlalchemyReviewRepository(session)
    output = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.OUTLINE,
        output_key="outline",
        version=1,
        schema_version="outline.v1",
        payload={"sections": []},
        idempotency_key="output:outline:1",
    )
    await repo.add_output(output)
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_output(
                create_review_output(
                    review_run_id=run.run_id,
                    output_type=ReviewOutputType.OUTLINE,
                    output_key="outline",
                    version=1,
                    schema_version="outline.v1",
                    payload={"sections": [{"section_key": "methods"}]},
                    idempotency_key="output:outline:duplicate-version",
                )
            )
            await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_output(
                create_review_output(
                    review_run_id=run.run_id,
                    output_type=ReviewOutputType.OUTLINE,
                    output_key="outline",
                    version=2,
                    schema_version="outline.v1",
                    payload={"sections": []},
                    idempotency_key="output:outline:1",
                )
            )
            await session.flush()

    await repo.add_human_input_request(
        create_human_input_request(
            review_run_id=run.run_id,
            request_version=1,
            outline_output_id=output.output_id,
            allowed_actions=[HumanInputAction.APPROVE],
        )
    )
    await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_human_input_request(
                create_human_input_request(
                    review_run_id=run.run_id,
                    request_version=2,
                    outline_output_id=output.output_id,
                    allowed_actions=[HumanInputAction.EDIT],
                )
            )
            await session.flush()


async def test_artifact_effectively_once_constraint(session, project: str) -> None:
    """同一 Review Run 的 Artifact 幂等键不能产生第二条记录。"""
    run, _ = await _seed_review(session, project)
    repo = SqlalchemyReviewRepository(session)
    artifact = create_artifact(
        review_run_id=run.run_id,
        project_id=project,
        owner_id="user-1",
        artifact_type=ArtifactType.REVIEW_MARKDOWN,
        storage_key=f"user-1/reviews/{run.run_id}/review.md",
        content_hash="a" * 64,
        size_bytes=100,
        media_type="text/markdown",
        idempotency_key="artifact:review",
    )
    await repo.add_artifact(artifact)
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repo.add_artifact(
                create_artifact(
                    review_run_id=run.run_id,
                    project_id=project,
                    owner_id="user-1",
                    artifact_type=ArtifactType.RUN_SUMMARY,
                    storage_key=f"user-1/reviews/{run.run_id}/summary.json",
                    content_hash="b" * 64,
                    size_bytes=80,
                    media_type="application/json",
                    idempotency_key="artifact:review",
                )
            )
            await session.flush()


async def test_review_creation_transaction_and_idempotency(db_engine) -> None:
    """真实数据库确认创建 bundle 同事务提交并可幂等重放。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project("user-1", "Review", "")
        await SqlalchemyProjectRepository(session).add(project)
        await session.commit()

    service = ReviewWorkflowService(
        session_factory=factory,
        project_repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        review_repo_factory=SqlalchemyReviewRepository,
    )
    first = await service.create_review_run(
        ActorContext("user-1"),
        project.project_id,
        "如何恢复固定 Workflow？",
        "create-review-1",
        "corr-1",
    )
    replay = await service.create_review_run(
        ActorContext("user-1"),
        project.project_id,
        "如何恢复固定 Workflow？",
        "create-review-1",
        "corr-2",
    )
    assert replay.run_id == first.run_id and replay.reused

    async with factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(first.run_id)
        review = await SqlalchemyReviewRepository(session).get_review_run_scoped(
            first.run_id, project.project_id, "user-1"
        )
        events = await SqlalchemyEventRepository(session).list_by_run(first.run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(first.run_id)
    assert run is not None and run.run_type == RunType.REVIEW.value
    assert review is not None
    assert review.current_stage is ReviewStage.FORMULATE_SEARCH_STRATEGY
    async with factory() as session:
        steps = await SqlalchemyReviewRepository(session).list_steps_scoped(
            first.run_id, project.project_id, "user-1"
        )
    assert [(item.step_key, item.status) for item in steps] == [
        (ReviewStepKey.VALIDATE_REQUEST, ReviewStepStatus.SUCCEEDED)
    ]
    assert [x.event_type for x in events] == ["review_run_created"]
    assert outbox is not None and outbox.status is OutboxStatus.PENDING


async def test_review_creation_rolls_back_whole_bundle_on_failure(db_engine) -> None:
    """Review 扩展写入失败时，通用 Run/Event/Outbox 不能部分提交。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project("user-1", "Review", "")
        await SqlalchemyProjectRepository(session).add(project)
        await session.commit()

    class _FailingReviewRepository(SqlalchemyReviewRepository):
        async def add_review_run(self, review_run):
            raise RuntimeError(f"模拟 ReviewRun 写入失败: {review_run.run_id}")

    service = ReviewWorkflowService(
        session_factory=factory,
        project_repo_factory=SqlalchemyProjectRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        review_repo_factory=_FailingReviewRepository,
    )
    with pytest.raises(RuntimeError, match="模拟 ReviewRun 写入失败"):
        await service.create_review_run(
            ActorContext("user-1"),
            project.project_id,
            "事务是否完整？",
            "create-review-failed",
            "corr-1",
        )

    async with factory() as session:
        run_count = await session.scalar(
            select(func.count()).select_from(RunORM).where(
                RunORM.project_id == project.project_id,
                RunORM.run_type == RunType.REVIEW.value,
            )
        )
    assert run_count == 0
