"""Outline 生成、人工提交和正常恢复的应用层测试。"""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from literature_agent.application.review_outline_service import (
    HumanOutlineInputService,
    ReviewOutlineDecisionService,
    ReviewOutlineService,
)
from literature_agent.domain.evidence import create_evidence
from literature_agent.domain.exceptions import (
    HumanInputConflictError,
    IdempotencyConflictError,
    ReviewOutlineInvalidError,
    ReviewOutlineScopeError,
)
from literature_agent.domain.model_types import ChatResult, ModelUsage
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.review import (
    HumanInputAction,
    HumanInputRequestStatus,
    ReviewOutputType,
    ReviewStepStatus,
    create_review_output,
    create_review_run,
    create_review_source,
)
from literature_agent.domain.run import RunStatus, RunType, create_run
from literature_agent.workflows.review_graph import (
    ReviewGraphFactory,
    ReviewGraphState,
    ReviewWorkflowRuntime,
)
from literature_agent.workflows.review_outline_nodes import ReviewOutlineGraphNodes
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_evidence_repository import FakeEvidenceRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_review_repository import FakeReviewRepository
from tests.fakes.fake_run_repository import FakeRunRepository

OUTLINE = {
    "sections": [
        {
            "section_key": "methods",
            "title": "主要方法",
            "purpose": "比较方法与限制",
            "dimension_keys": ["method", "limitations"],
        }
    ]
}


class _Gateway:
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = responses or [OUTLINE]
        self.calls = []

    async def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ChatResult(
            content=json.dumps(self.responses[len(self.calls) - 1], ensure_ascii=False),
            model="fake",
            usage=ModelUsage(),
        )


async def _seed():
    run_repo = FakeRunRepository()
    review_repo = FakeReviewRepository()
    evidence_repo = FakeEvidenceRepository()
    event_repo = FakeEventRepository()
    outbox_repo = FakeOutboxRepository()
    run = replace(
        create_run("project-1", "user-1", RunType.REVIEW),
        run_id="review-1",
        status=RunStatus.RUNNING,
        event_sequence=4,
    )
    await run_repo.add(run)
    review_repo.authorize_run(run.run_id, "project-1", "user-1")
    review = create_review_run(
        run_id=run.run_id,
        research_question="比较 Agent 方法",
        workflow_version="review.v1",
        model_profile_version="review-default.v1",
        prompt_versions={
            "search_strategy": "search_strategy.v1",
            "evidence_extract": "review-evidence-extraction.v1",
            "outline_generate": "outline_generate.v1",
        },
        config_snapshot={"source_limit": 10},
    )
    await review_repo.add_review_run(review)
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
    await review_repo.add_output(strategy)
    source = create_review_source(
        review_run_id=run.run_id,
        arxiv_id="2401.1",
        arxiv_version="v1",
        rank=1,
        metadata_snapshot={"title": "论文", "untrusted_extra": "不得进入 Prompt"},
    ).mark_ready("paper-1", "version-1")
    await review_repo.add_source(source)
    evidence = create_evidence(
        run_id=run.run_id,
        project_id="project-1",
        paper_id="paper-1",
        version_id="version-1",
        parse_revision_id="revision-1",
        chunk_id="chunk-1",
        section_path="Methods",
        page_start=1,
        page_end=1,
        excerpt="证据",
    )
    await evidence_repo.add_many([evidence])
    matrix = create_review_output(
        review_run_id=run.run_id,
        output_type=ReviewOutputType.EVIDENCE_MATRIX,
        output_key="evidence-matrix",
        version=1,
        schema_version="evidence-matrix.v1",
        payload={
            "rows": [
                {
                    "paper_id": "paper-1",
                    "dimension_key": "method",
                    "status": "extracted",
                    "finding": "使用方法 A",
                    "limitations": "数据较少",
                    "evidence_ids": [evidence.evidence_id],
                },
                {
                    "paper_id": "paper-1",
                    "dimension_key": "limitations",
                    "status": "extracted",
                    "finding": "数据较少",
                    "limitations": None,
                    "evidence_ids": [evidence.evidence_id],
                },
                {
                    "paper_id": "paper-1",
                    "dimension_key": "evaluation",
                    "status": "insufficient_evidence",
                    "finding": None,
                    "limitations": None,
                    "evidence_ids": [],
                },
            ],
            "paper_failures": [],
            "summary": {"valid_papers": 1, "failed_papers": 0},
        },
        idempotency_key="matrix",
    )
    await review_repo.add_output(matrix)
    outbox = create_outbox_entry(run.run_id)
    await outbox_repo.add(outbox)
    assert await outbox_repo.try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
    return {
        "run_repo": run_repo,
        "review_repo": review_repo,
        "evidence_repo": evidence_repo,
        "event_repo": event_repo,
        "outbox_repo": outbox_repo,
        "strategy": strategy,
        "matrix": matrix,
    }


def _outline_service(data, gateway):
    return ReviewOutlineService(
        session_factory=fake_session,
        run_repo_factory=lambda _: data["run_repo"],
        review_repo_factory=lambda _: data["review_repo"],
        evidence_repo_factory=lambda _: data["evidence_repo"],
        event_repo_factory=lambda _: data["event_repo"],
        model_gateway=gateway,
    )


def _input_service(data):
    return HumanOutlineInputService(
        session_factory=fake_session,
        run_repo_factory=lambda _: data["run_repo"],
        review_repo_factory=lambda _: data["review_repo"],
        event_repo_factory=lambda _: data["event_repo"],
        outbox_repo_factory=lambda _: data["outbox_repo"],
    )


async def test_proposal_persists_output_request_events_and_waiting_state() -> None:
    data = await _seed()
    gateway = _Gateway()

    result = await _outline_service(data, gateway).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )

    assert result.output.payload == OUTLINE
    assert result.request.outline_output_id == result.output.output_id
    assert result.model_invocations == 1
    run = await data["run_repo"].get_by_id("review-1")
    assert run.status is RunStatus.WAITING_INPUT
    assert run.event_sequence == 6
    assert [event.event_type for event in await data["event_repo"].list_by_run("review-1")] == [
        "outline_proposed",
        "human_input_requested",
    ]
    model_payload = json.loads(gateway.calls[0][0][1].content)
    assert set(model_payload) == {
        "prompt_version",
        "research_question",
        "dimensions",
        "evidence_matrix_summary",
        "paper_coverage",
        "feedback_round",
        "feedback",
    }
    assert "evidence_ids" not in model_payload["evidence_matrix_summary"][0]
    assert "untrusted_extra" not in gateway.calls[0][0][1].content


async def test_crash_after_outline_output_replays_without_second_model_call() -> None:
    data = await _seed()
    gateway = _Gateway()

    class _CrashAfterOutput(ReviewOutlineService):
        async def _persist_request_and_pause(self, **_kwargs):
            raise RuntimeError("模拟 Output 提交后崩溃")

    crashing = _CrashAfterOutput(
        session_factory=fake_session,
        run_repo_factory=lambda _: data["run_repo"],
        review_repo_factory=lambda _: data["review_repo"],
        evidence_repo_factory=lambda _: data["evidence_repo"],
        event_repo_factory=lambda _: data["event_repo"],
        model_gateway=gateway,
    )
    kwargs = {
        "run_id": "review-1",
        "project_id": "project-1",
        "owner_id": "user-1",
        "search_strategy_output_id": data["strategy"].output_id,
        "evidence_matrix_output_id": data["matrix"].output_id,
        "feedback_round": 0,
        "correlation_id": "outline-1",
    }
    with pytest.raises(RuntimeError, match="模拟"):
        await crashing.propose_and_pause(**kwargs)
    assert len(gateway.calls) == 1
    assert data["review_repo"].requests == []

    replay = await _outline_service(data, gateway).propose_and_pause(**kwargs)
    assert replay.model_invocations == 0
    assert len(gateway.calls) == 1
    assert len(data["review_repo"].requests) == 1
    assert len(await data["event_repo"].list_by_run("review-1")) == 2


async def test_matrix_allows_same_evidence_across_dimensions_but_rejects_duplicate_in_row() -> None:
    data = await _seed()
    # seed 中 method/limitations 两行共享 Evidence，属于合法的跨维度复用。
    await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )

    invalid = await _seed()
    row = invalid["matrix"].payload["rows"][0]
    row["evidence_ids"] = row["evidence_ids"] * 2
    gateway = _Gateway()
    with pytest.raises(ReviewOutlineScopeError, match="行结构"):
        await _outline_service(invalid, gateway).propose_and_pause(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=invalid["strategy"].output_id,
            evidence_matrix_output_id=invalid["matrix"].output_id,
            feedback_round=0,
            correlation_id="outline-1",
        )
    assert gateway.calls == []


async def test_invalid_model_outline_does_not_create_waiting_request() -> None:
    data = await _seed()
    gateway = _Gateway(
        [
            {
                "sections": [
                    {
                        "section_key": "unknown",
                        "title": "未知",
                        "purpose": "非法维度",
                        "dimension_keys": ["unknown"],
                    }
                ]
            }
        ]
    )
    with pytest.raises(ReviewOutlineInvalidError):
        await _outline_service(data, gateway).propose_and_pause(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            feedback_round=0,
            correlation_id="outline-invalid",
        )
    assert data["review_repo"].requests == []
    assert (await data["run_repo"].get_by_id("review-1")).status is RunStatus.RUNNING


async def test_waiting_input_replay_rejects_request_semantic_conflict() -> None:
    data = await _seed()
    service = _outline_service(data, _Gateway())
    proposal = await service.propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    data["review_repo"].requests[0] = replace(
        proposal.request, allowed_actions=(HumanInputAction.APPROVE,)
    )
    with pytest.raises(IdempotencyConflictError, match="outline-request"):
        await service._persist_request_and_pause(
            output=proposal.output,
            project_id="project-1",
            owner_id="user-1",
            correlation_id="replay",
            expected_previous_outline_id=None,
        )


async def test_waiting_input_replay_rejects_step_output_conflict() -> None:
    data = await _seed()
    service = _outline_service(data, _Gateway())
    proposal = await service.propose_and_pause(
        run_id="review-1", project_id="project-1", owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0, correlation_id="outline-1",
    )
    index = next(
        i for i, step in enumerate(data["review_repo"].steps)
        if step.status is ReviewStepStatus.PAUSED
    )
    data["review_repo"].steps[index] = replace(
        data["review_repo"].steps[index], output_refs={"request_id": "stale"}
    )
    with pytest.raises(ReviewOutlineScopeError, match="Step"):
        await service._persist_request_and_pause(
            output=proposal.output, project_id="project-1", owner_id="user-1",
            correlation_id="replay", expected_previous_outline_id=None,
        )


@pytest.mark.parametrize("action", [HumanInputAction.APPROVE, HumanInputAction.EDIT])
async def test_approve_or_edit_resolves_request_and_schedules_without_retry(action) -> None:
    data = await _seed()
    proposal = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    payload = {} if action is HumanInputAction.APPROVE else OUTLINE

    submitted = await _input_service(data).submit(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        request_id=proposal.request.request_id,
        request_version=proposal.request.request_version,
        outline_output_id=proposal.output.output_id,
        action=action,
        payload=payload,
        idempotency_key="input-1",
        correlation_id="input-1",
    )

    assert submitted.approved_outline_output_id is not None
    if action is HumanInputAction.EDIT:
        assert submitted.human_input.payload == {
            "approved_outline_output_id": submitted.approved_outline_output_id
        }
    assert (await data["run_repo"].get_by_id("review-1")).status is RunStatus.QUEUED
    outbox = await data["outbox_repo"].get_by_run_id("review-1")
    assert outbox.status is OutboxStatus.PENDING
    assert outbox.attempt_count == 0
    assert (await data["event_repo"].list_by_run("review-1"))[-1].event_type == (
        "human_input_submitted"
    )


async def test_large_valid_edit_stores_outline_only_in_versioned_output() -> None:
    data = await _seed()
    proposal = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1", project_id="project-1", owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0, correlation_id="outline-1",
    )
    large_outline = {
        "sections": [
            {
                "section_key": f"section_{index}",
                "title": "题" * 200,
                "purpose": "限" * 1000,
                "dimension_keys": ["method", "limitations"],
            }
            for index in range(12)
        ]
    }
    result = await _input_service(data).submit(
        run_id="review-1", project_id="project-1", owner_id="user-1",
        request_id=proposal.request.request_id, request_version=1,
        outline_output_id=proposal.output.output_id, action=HumanInputAction.EDIT,
        payload=large_outline, idempotency_key="large-edit", correlation_id="input-1",
    )
    assert result.human_input.payload == {
        "approved_outline_output_id": result.approved_outline_output_id
    }
    approved = next(
        item for item in data["review_repo"].outputs
        if item.output_id == result.approved_outline_output_id
    )
    assert approved.payload == large_outline


async def test_same_idempotency_replays_and_different_semantics_conflict() -> None:
    data = await _seed()
    proposal = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    service = _input_service(data)
    kwargs = {
        "run_id": "review-1",
        "project_id": "project-1",
        "owner_id": "user-1",
        "request_id": proposal.request.request_id,
        "request_version": 1,
        "outline_output_id": proposal.output.output_id,
        "action": HumanInputAction.FEEDBACK,
        "payload": {"feedback": "增加限制比较"},
        "idempotency_key": "same-key",
        "correlation_id": "input-1",
    }
    first = await service.submit(**kwargs)
    replay = await service.submit(**kwargs)
    assert replay.replayed is True
    assert replay.human_input.human_input_id == first.human_input.human_input_id

    with pytest.raises(HumanInputConflictError, match="idempotency"):
        await service.submit(**{**kwargs, "payload": {"feedback": "不同反馈"}})


async def test_idempotency_replay_rejects_broken_request_resolution() -> None:
    data = await _seed()
    proposal = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1", project_id="project-1", owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0, correlation_id="outline-1",
    )
    service = _input_service(data)
    kwargs = {
        "run_id": "review-1",
        "project_id": "project-1",
        "owner_id": "user-1",
        "request_id": proposal.request.request_id,
        "request_version": 1,
        "outline_output_id": proposal.output.output_id,
        "action": HumanInputAction.APPROVE,
        "payload": {},
        "idempotency_key": "broken-replay",
        "correlation_id": "input-1",
    }
    await service.submit(**kwargs)
    request = data["review_repo"].requests[0]
    data["review_repo"].requests[0] = replace(
        request, status=HumanInputRequestStatus.OPEN, resolved_input_id=None, resolved_at=None
    )
    with pytest.raises(HumanInputConflictError, match="idempotency"):
        await service.submit(**kwargs)


async def test_submit_rejects_overlong_idempotency_key_before_persistence() -> None:
    data = await _seed()
    with pytest.raises(HumanInputConflictError, match="idempotency_key"):
        await _input_service(data).submit(
            run_id="review-1", project_id="project-1", owner_id="user-1",
            request_id="request", request_version=1, outline_output_id="outline",
            action=HumanInputAction.APPROVE, payload={}, idempotency_key="x" * 256,
            correlation_id="input-1",
        )


async def test_new_submission_requires_paused_outline_step() -> None:
    data = await _seed()
    proposal = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1", project_id="project-1", owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0, correlation_id="outline-1",
    )
    index = next(
        i
        for i, step in enumerate(data["review_repo"].steps)
        if step.status is ReviewStepStatus.PAUSED
    )
    data["review_repo"].steps[index] = replace(
        data["review_repo"].steps[index], status=ReviewStepStatus.RUNNING
    )
    with pytest.raises(HumanInputConflictError, match="step_not_paused"):
        await _input_service(data).submit(
            run_id="review-1", project_id="project-1", owner_id="user-1",
            request_id=proposal.request.request_id, request_version=1,
            outline_output_id=proposal.output.output_id, action=HumanInputAction.APPROVE,
            payload={}, idempotency_key="step-invalid", correlation_id="input-1",
        )


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        (HumanInputAction.APPROVE, {"unexpected": True}),
        (HumanInputAction.EDIT, {**OUTLINE, "unknown": True}),
        (
            HumanInputAction.EDIT,
            {"sections": [{**OUTLINE["sections"][0], "dimension_keys": ["unknown"]}]},
        ),
        (HumanInputAction.FEEDBACK, {"feedback": " "}),
        (HumanInputAction.FEEDBACK, {"feedback": "x" * 4001}),
    ],
)
async def test_submit_rejects_invalid_action_payload(action, payload) -> None:
    data = await _seed()
    proposal = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    with pytest.raises(HumanInputConflictError):
        await _input_service(data).submit(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            request_id=proposal.request.request_id,
            request_version=1,
            outline_output_id=proposal.output.output_id,
            action=action,
            payload=payload,
            idempotency_key="invalid-input",
            correlation_id="invalid-input",
        )


@pytest.mark.parametrize(
    ("project_id", "owner_id", "request_version"),
    [
        ("other-project", "user-1", 1),
        ("project-1", "other-user", 1),
        ("project-1", "user-1", 2),
    ],
)
async def test_submit_rejects_cross_scope_and_stale_version(
    project_id, owner_id, request_version
) -> None:
    data = await _seed()
    proposal = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    with pytest.raises(HumanInputConflictError):
        await _input_service(data).submit(
            run_id="review-1",
            project_id=project_id,
            owner_id=owner_id,
            request_id=proposal.request.request_id,
            request_version=request_version,
            outline_output_id=proposal.output.output_id,
            action="approve",
            payload={},
            idempotency_key="invalid-scope",
            correlation_id="invalid-scope",
        )


async def test_feedback_uses_persisted_input_and_creates_next_version_once() -> None:
    data = await _seed()
    gateway = _Gateway([OUTLINE, OUTLINE])
    service = _outline_service(data, gateway)
    first = await service.propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    submitted = await _input_service(data).submit(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        request_id=first.request.request_id,
        request_version=1,
        outline_output_id=first.output.output_id,
        action=HumanInputAction.FEEDBACK,
        payload={"feedback": "增加限制比较"},
        idempotency_key="feedback-1",
        correlation_id="feedback-1",
    )
    queued = await data["run_repo"].get_by_id("review-1")
    await data["run_repo"].update_status(
        "review-1", RunStatus.QUEUED, RunStatus.RUNNING, queued.event_sequence
    )
    outbox = await data["outbox_repo"].get_by_run_id("review-1")
    assert await data["outbox_repo"].try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))

    request_index = next(
        i for i, request in enumerate(data["review_repo"].requests)
        if request.request_id == first.request.request_id
    )
    resolved_request = data["review_repo"].requests[request_index]
    data["review_repo"].requests[request_index] = replace(
        resolved_request, resolved_input_id="stale-input"
    )
    with pytest.raises(ReviewOutlineScopeError, match="不闭合"):
        await service.propose_and_pause(
            run_id="review-1", project_id="project-1", owner_id="user-1",
            search_strategy_output_id=data["strategy"].output_id,
            evidence_matrix_output_id=data["matrix"].output_id,
            feedback_round=1, correlation_id="outline-stale",
            feedback_human_input_id=submitted.human_input.human_input_id,
        )
    assert len(gateway.calls) == 1
    data["review_repo"].requests[request_index] = resolved_request

    second = await service.propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=1,
        correlation_id="outline-2",
        feedback_human_input_id=submitted.human_input.human_input_id,
    )

    assert second.output.version == 2
    assert second.request.request_version == 2
    assert len(gateway.calls) == 2
    second_context = json.loads(gateway.calls[1][0][1].content)
    assert second_context["feedback"] == "增加限制比较"


async def test_decision_loader_rejects_cross_owner_and_returns_persisted_feedback() -> None:
    data = await _seed()
    first = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    submitted = await _input_service(data).submit(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        request_id=first.request.request_id,
        request_version=1,
        outline_output_id=first.output.output_id,
        action="feedback",
        payload={"feedback": "补充评测"},
        idempotency_key="feedback-1",
        correlation_id="feedback-1",
    )
    loader = ReviewOutlineDecisionService(
        session_factory=fake_session,
        review_repo_factory=lambda _: data["review_repo"],
    )
    decision = await loader.load(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        request_id=first.request.request_id,
        human_input_id=submitted.human_input.human_input_id,
    )
    assert decision.feedback == "补充评测"
    with pytest.raises(ReviewOutlineScopeError):
        await loader.load(
            run_id="review-1",
            project_id="project-1",
            owner_id="other",
            request_id=first.request.request_id,
            human_input_id=submitted.human_input.human_input_id,
        )


async def test_decision_loader_rejects_outline_ids_outside_scoped_outputs() -> None:
    data = await _seed()
    first = await _outline_service(data, _Gateway()).propose_and_pause(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
        correlation_id="outline-1",
    )
    submitted = await _input_service(data).submit(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        request_id=first.request.request_id,
        request_version=1,
        outline_output_id=first.output.output_id,
        action="approve",
        payload={},
        idempotency_key="approve-1",
        correlation_id="approve-1",
    )
    loader = ReviewOutlineDecisionService(
        session_factory=fake_session,
        review_repo_factory=lambda _: data["review_repo"],
    )
    data["review_repo"].inputs[0] = replace(
        submitted.human_input,
        payload={"approved_outline_output_id": "outline-from-another-run"},
    )

    with pytest.raises(ReviewOutlineScopeError, match="持久 Outline Output"):
        await loader.load(
            run_id="review-1",
            project_id="project-1",
            owner_id="user-1",
            request_id=first.request.request_id,
            human_input_id=submitted.human_input.human_input_id,
        )


async def test_real_outline_services_drive_feedback_interrupt_loop_and_approve_boundary() -> None:
    data = await _seed()
    gateway = _Gateway([OUTLINE, OUTLINE])
    decision_service = ReviewOutlineDecisionService(
        session_factory=fake_session,
        review_repo_factory=lambda _: data["review_repo"],
    )
    nodes = ReviewOutlineGraphNodes(
        owner_id="user-1",
        outline_service=_outline_service(data, gateway),
        decision_service=decision_service,
    )
    saver = InMemorySaver()
    runtime = ReviewWorkflowRuntime(
        ReviewGraphFactory(
            outline_entry_node=nodes.propose,
            outline_decision_node=nodes.apply_decision,
        ),
        saver,
    )
    state = ReviewGraphState(
        review_run_id="review-1",
        project_id="project-1",
        workflow_version="review.v1",
        search_strategy_output_id=data["strategy"].output_id,
        evidence_matrix_output_id=data["matrix"].output_id,
        feedback_round=0,
    )
    first_pause = await runtime.start(state)
    first_request = data["review_repo"].requests[0]
    feedback = await _input_service(data).submit(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        request_id=first_request.request_id,
        request_version=1,
        outline_output_id=first_pause["outline_output_id"],
        action="feedback",
        payload={"feedback": "补充评测"},
        idempotency_key="graph-feedback",
        correlation_id="graph-feedback",
    )
    await _claim_resumed_run(data)
    second_pause = await runtime.resume_human_input(
        "review-1",
        request_id=first_request.request_id,
        human_input_id=feedback.human_input.human_input_id,
    )
    assert second_pause["feedback_round"] == 1
    assert len(gateway.calls) == 2
    second_request = data["review_repo"].requests[1]
    approved = await _input_service(data).submit(
        run_id="review-1",
        project_id="project-1",
        owner_id="user-1",
        request_id=second_request.request_id,
        request_version=2,
        outline_output_id=second_pause["outline_output_id"],
        action="approve",
        payload={},
        idempotency_key="graph-approve",
        correlation_id="graph-approve",
    )
    await _claim_resumed_run(data)
    restarted = ReviewWorkflowRuntime(
        ReviewGraphFactory(
            outline_entry_node=nodes.propose,
            outline_decision_node=nodes.apply_decision,
        ),
        saver,
    )
    completed = await restarted.resume_human_input(
        "review-1",
        request_id=second_request.request_id,
        human_input_id=approved.human_input.human_input_id,
    )
    assert completed["approved_outline_output_id"] == second_pause["outline_output_id"]
    assert completed["outline_boundary_reached"] is True
    assert (await data["run_repo"].get_by_id("review-1")).status is RunStatus.RUNNING


async def _claim_resumed_run(data) -> None:
    queued = await data["run_repo"].get_by_id("review-1")
    assert queued.status is RunStatus.QUEUED
    assert await data["run_repo"].update_status(
        "review-1", RunStatus.QUEUED, RunStatus.RUNNING, queued.event_sequence
    )
    outbox = await data["outbox_repo"].get_by_run_id("review-1")
    assert outbox.status is OutboxStatus.PENDING
    assert await data["outbox_repo"].try_mark_dispatched(outbox.outbox_id, datetime.now(UTC))
