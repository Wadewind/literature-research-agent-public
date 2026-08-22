"""HumanInput、Run、Event 与 Outbox 的 PostgreSQL 原子恢复测试。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.application.review_outline_service import HumanOutlineInputService
from literature_agent.domain.exceptions import HumanInputConflictError, RunSchedulingError
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.review import (
    HumanInputAction,
    ReviewOutputType,
    ReviewStage,
    ReviewStepKey,
    create_human_input_request,
    create_review_output,
    create_review_run,
    create_run_step,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository


async def _seed_waiting(factory, project_id: str, *, dispatched: bool = True):
    async with factory() as session:
        run = replace(
            create_run(project_id, "user-1", RunType.REVIEW),
            status=RunStatus.WAITING_INPUT,
        )
        run_repo = SqlalchemyRunRepository(session)
        review_repo = SqlalchemyReviewRepository(session)
        await run_repo.add(run)
        await session.flush()
        review = create_review_run(
            run_id=run.run_id,
            research_question="可靠 HITL",
            workflow_version="review.v1",
            model_profile_version="review-default.v1",
            prompt_versions={"outline_generate": "outline_generate.v1"},
            config_snapshot={},
        )
        await review_repo.add_review_run(review)
        await session.flush()
        strategy = create_review_output(
            review_run_id=run.run_id,
            output_type=ReviewOutputType.SEARCH_STRATEGY,
            output_key="search-strategy",
            version=1,
            schema_version="search-strategy.v1",
            payload={
                "dimensions": [
                    {"dimension_key": "method", "name": "方法", "extraction_question": "方法？"},
                    {
                        "dimension_key": "limitations",
                        "name": "限制",
                        "extraction_question": "限制？",
                    },
                    {
                        "dimension_key": "evaluation",
                        "name": "评测",
                        "extraction_question": "评测？",
                    },
                ]
            },
            idempotency_key="strategy",
        )
        outline = create_review_output(
            review_run_id=run.run_id,
            output_type=ReviewOutputType.OUTLINE,
            output_key="outline",
            version=1,
            schema_version="outline.v1",
            payload={
                "sections": [
                    {
                        "section_key": "methods",
                        "title": "方法",
                        "purpose": "比较方法",
                        "dimension_keys": ["method"],
                    }
                ]
            },
            idempotency_key="outline-1",
        )
        await review_repo.add_output(strategy)
        await review_repo.add_output(outline)
        await session.flush()
        advanced = replace(
            review,
            current_stage=ReviewStage.REVIEW_OUTLINE,
            current_outline_output_id=outline.output_id,
            updated_at=datetime.now(UTC),
        )
        assert await review_repo.advance_review_outline(advanced, expected_outline_output_id=None)
        request = create_human_input_request(
            review_run_id=run.run_id,
            request_version=1,
            outline_output_id=outline.output_id,
            allowed_actions=list(HumanInputAction),
        )
        await review_repo.add_human_input_request(request)
        step = (
            create_run_step(
                run_id=run.run_id,
                step_key=ReviewStepKey.REVIEW_OUTLINE,
                sequence=9,
                idempotency_key=f"{run.run_id}:review-outline",
            )
            .start()
            .pause({"request_id": request.request_id})
        )
        await review_repo.add_step(step)
        outbox = create_outbox_entry(run.run_id)
        outbox_repo = SqlalchemyOutboxRepository(session)
        await outbox_repo.add(outbox)
        await session.flush()
        if dispatched:
            assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
        await session.commit()
        return run, request, outline


def _service(factory):
    return HumanOutlineInputService(
        session_factory=factory,
        run_repo_factory=SqlalchemyRunRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
    )


def _submit_kwargs(run, request, outline, *, key: str):
    return {
        "run_id": run.run_id,
        "project_id": run.project_id,
        "owner_id": run.owner_id,
        "request_id": request.request_id,
        "request_version": request.request_version,
        "outline_output_id": outline.output_id,
        "action": HumanInputAction.APPROVE,
        "payload": {},
        "idempotency_key": key,
        "correlation_id": key,
    }


async def test_concurrent_human_inputs_have_one_business_effect(db_engine, project) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run, request, outline = await _seed_waiting(factory, project)
    service = _service(factory)

    results = await asyncio.gather(
        service.submit(**_submit_kwargs(run, request, outline, key="submit-1")),
        service.submit(**_submit_kwargs(run, request, outline, key="submit-2")),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert any(isinstance(item, HumanInputConflictError) for item in results)
    async with factory() as session:
        run_after = await SqlalchemyRunRepository(session).get_by_id(run.run_id)
        repo = SqlalchemyReviewRepository(session)
        request_after = await repo.get_human_input_request_scoped_for_update(
            request.request_id, run.run_id, project, "user-1"
        )
        events = await SqlalchemyEventRepository(session).list_by_run(run.run_id)
        outbox = await SqlalchemyOutboxRepository(session).get_by_run_id(run.run_id)
        assert run_after.status is RunStatus.QUEUED
        assert request_after.status.value == "resolved"
        assert len(events) == 1 and events[0].event_type == "human_input_submitted"
        assert outbox.status is OutboxStatus.PENDING
        assert outbox.attempt_count == 0


async def test_schedule_failure_rolls_back_human_input_request_run_and_event(
    db_engine, project
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    run, request, outline = await _seed_waiting(factory, project, dispatched=False)

    with pytest.raises(RunSchedulingError):
        await _service(factory).submit(
            **_submit_kwargs(run, request, outline, key="submit-rollback")
        )

    async with factory() as session:
        run_after = await SqlalchemyRunRepository(session).get_by_id(run.run_id)
        repo = SqlalchemyReviewRepository(session)
        request_after = await repo.get_human_input_request_scoped_for_update(
            request.request_id, run.run_id, project, "user-1"
        )
        events = await SqlalchemyEventRepository(session).list_by_run(run.run_id)
        replay = await repo.get_human_input_by_idempotency_scoped(
            "user-1", "submit-rollback", run.run_id, project, "user-1"
        )
        assert run_after.status is RunStatus.WAITING_INPUT
        assert request_after.status.value == "open"
        assert replay is None
        assert events == []
        steps = await repo.list_steps_scoped(run.run_id, project, "user-1")
        assert len(steps) == 1 and steps[0].status.value == "paused"
