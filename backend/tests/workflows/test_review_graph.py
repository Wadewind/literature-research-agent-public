"""Review LangGraph Runtime 契约测试。"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from psycopg import OperationalError

from literature_agent.domain.exceptions import (
    CheckpointDataError,
    CheckpointUnavailableError,
)
from literature_agent.workflows.review_graph import (
    ReviewGraphFactory,
    ReviewGraphState,
    ReviewWorkflowRuntime,
    review_graph_config,
    review_thread_id,
)


def _state(run_id: str) -> ReviewGraphState:
    return ReviewGraphState(
        review_run_id=run_id,
        project_id="project-1",
        workflow_version="review.v1",
        research_question="如何恢复？",
    )


def test_thread_id_and_namespace_are_stable_and_unambiguous() -> None:
    assert review_thread_id("run-1") == "review.v1:review-run:run-1"
    assert review_graph_config("run-1")["configurable"] == {
        "thread_id": "review.v1:review-run:run-1",
        "checkpoint_ns": "",
    }
    with pytest.raises(ValueError):
        review_thread_id("bad:id")


async def test_resume_uses_pending_checkpoint_and_reuses_idempotent_side_effect() -> None:
    """副作用提交后节点崩溃，重建 Runtime 后恢复只复用相同业务结果。"""
    saver = InMemorySaver()
    stored: dict[str, str] = {}
    calls = 0

    async def node(state: ReviewGraphState) -> dict:
        nonlocal calls
        calls += 1
        key = f"{state['review_run_id']}:slice-boundary"
        output_id = stored.setdefault(key, "output-1")
        if calls == 1:
            raise RuntimeError("模拟业务副作用提交后的进程崩溃")
        return {"search_strategy_output_id": output_id}

    first = ReviewWorkflowRuntime(ReviewGraphFactory(node), saver)
    with pytest.raises(RuntimeError):
        await first.start(_state("run-1"))

    second = ReviewWorkflowRuntime(ReviewGraphFactory(node), saver)
    result = await second.resume("run-1")
    assert result["search_strategy_output_id"] == "output-1"
    assert calls == 2
    assert len(stored) == 1

    # 已完成 Thread 再 resume 只读取最终 checkpoint，不开启新一轮节点执行。
    repeated = await second.resume("run-1")
    assert repeated["search_strategy_output_id"] == "output-1"
    assert calls == 2


async def test_threads_are_isolated() -> None:
    saver = InMemorySaver()

    async def node(state: ReviewGraphState) -> dict:
        return {"search_strategy_output_id": f"output:{state['review_run_id']}"}

    runtime = ReviewWorkflowRuntime(ReviewGraphFactory(node), saver)
    one = await runtime.start(_state("run-1"))
    two = await runtime.start(_state("run-2"))
    assert one["search_strategy_output_id"] == "output:run-1"
    assert two["search_strategy_output_id"] == "output:run-2"


async def test_workflow_version_mismatch_is_permanent_data_error() -> None:
    async def node(_state: ReviewGraphState) -> dict:
        return {}

    runtime = ReviewWorkflowRuntime(ReviewGraphFactory(node), InMemorySaver())
    state = _state("run-1")
    state["workflow_version"] = "review.v2"
    with pytest.raises(CheckpointDataError):
        await runtime.start(state)


async def test_missing_resume_checkpoint_is_permanent_data_error() -> None:
    """不存在的 Thread 不能被当成临时故障无限重试。"""

    async def node(_state: ReviewGraphState) -> dict:
        return {}

    runtime = ReviewWorkflowRuntime(ReviewGraphFactory(node), InMemorySaver())
    with pytest.raises(CheckpointDataError):
        await runtime.resume("missing-run")


async def test_postgres_checkpoint_failure_is_classified_as_temporary() -> None:
    class BrokenSaver(InMemorySaver):
        async def aget_tuple(self, config):
            raise OperationalError("database unavailable")

    async def node(_state: ReviewGraphState) -> dict:
        return {}

    runtime = ReviewWorkflowRuntime(ReviewGraphFactory(node), BrokenSaver())
    with pytest.raises(CheckpointUnavailableError):
        await runtime.start(_state("run-1"))


async def test_postgres_checkpoint_write_failure_is_classified_as_temporary() -> None:
    class BrokenSaver(InMemorySaver):
        async def aput(self, config, checkpoint, metadata, new_versions):
            raise OperationalError("database unavailable during write")

    async def node(_state: ReviewGraphState) -> dict:
        return {}

    runtime = ReviewWorkflowRuntime(ReviewGraphFactory(node), BrokenSaver())
    with pytest.raises(CheckpointUnavailableError):
        await runtime.start(_state("run-1"))


async def test_node_value_error_is_not_misclassified_as_checkpoint_error() -> None:
    """节点业务异常必须原样交给 Run 失败策略，不能伪装成 checkpoint 损坏。"""
    error = ValueError("业务输入无效")

    async def node(_state: ReviewGraphState) -> dict:
        raise error

    runtime = ReviewWorkflowRuntime(ReviewGraphFactory(node), InMemorySaver())
    with pytest.raises(ValueError) as raised:
        await runtime.start(_state("run-1"))
    assert raised.value is error


async def test_outline_interrupt_approve_resumes_with_persisted_ids() -> None:
    saver = InMemorySaver()
    calls = {"entry": 0, "decision": 0}

    async def entry(_state: ReviewGraphState) -> dict:
        calls["entry"] += 1
        return {
            "outline_output_id": "outline-1",
            "human_input_request_id": "request-1",
            "feedback_round": 0,
        }

    async def decision(state: ReviewGraphState) -> dict:
        calls["decision"] += 1
        assert state["human_input_request_id"] == "request-1"
        assert state["human_input_id"] == "input-1"
        return {
            "outline_action": "approve",
            "approved_outline_output_id": "outline-1",
        }

    first_runtime = ReviewWorkflowRuntime(
        ReviewGraphFactory(
            outline_entry_node=entry,
            outline_decision_node=decision,
        ),
        saver,
    )
    paused = await first_runtime.start(_state("review-1"))
    assert paused["human_input_request_id"] == "request-1"
    assert calls == {"entry": 1, "decision": 0}

    # 使用同一 Saver 重建 Runtime，模拟进程重启后 Command(resume=...)。
    restarted = ReviewWorkflowRuntime(
        ReviewGraphFactory(
            outline_entry_node=entry,
            outline_decision_node=decision,
        ),
        saver,
    )
    resumed = await restarted.resume_human_input(
        "review-1", request_id="request-1", human_input_id="input-1"
    )
    assert resumed["approved_outline_output_id"] == "outline-1"
    assert resumed["outline_boundary_reached"] is True
    assert calls == {"entry": 1, "decision": 1}


async def test_outline_feedback_loops_to_new_request_and_interrupts_again() -> None:
    saver = InMemorySaver()
    entry_calls = 0

    async def entry(state: ReviewGraphState) -> dict:
        nonlocal entry_calls
        entry_calls += 1
        version = state.get("feedback_round", 0) + 1
        return {
            "outline_output_id": f"outline-{version}",
            "human_input_request_id": f"request-{version}",
        }

    async def decision(state: ReviewGraphState) -> dict:
        if state["human_input_id"] == "feedback-1":
            return {
                "outline_action": "feedback",
                "feedback_round": 1,
                "feedback_human_input_id": "feedback-1",
            }
        return {
            "outline_action": "edit",
            "approved_outline_output_id": "outline-edited",
        }

    runtime = ReviewWorkflowRuntime(
        ReviewGraphFactory(
            outline_entry_node=entry,
            outline_decision_node=decision,
        ),
        saver,
    )
    await runtime.start(_state("review-2"))
    second_pause = await runtime.resume_human_input(
        "review-2", request_id="request-1", human_input_id="feedback-1"
    )
    assert second_pause["human_input_request_id"] == "request-2"
    assert second_pause["outline_output_id"] == "outline-2"
    assert entry_calls == 2

    completed = await runtime.resume_human_input(
        "review-2", request_id="request-2", human_input_id="edit-2"
    )
    assert completed["approved_outline_output_id"] == "outline-edited"
    assert completed["outline_boundary_reached"] is True
    assert entry_calls == 2


def test_approved_outline_connects_sections_to_safe_export_boundary() -> None:
    async def entry(_state):
        return {"outline_output_id": "outline-1", "human_input_request_id": "request-1"}

    async def decision(_state):
        return {"outline_action": "approve", "approved_outline_output_id": "outline-1"}

    async def draft(_state):
        return {"section_output_ids": ["section-1"]}

    async def validate(_state):
        return {"claim_set_id": "claims-1"}

    async def consistency(_state):
        return {"consistency_output_id": "consistency-1"}

    graph = ReviewGraphFactory(
        outline_entry_node=entry,
        outline_decision_node=decision,
        section_draft_node=draft,
        section_validate_node=validate,
        consistency_node=consistency,
    ).compile(InMemorySaver())
    mermaid = graph.get_graph().draw_mermaid()

    assert "apply_outline_decision -. &nbsp;approved&nbsp; .-> draft_sections" in mermaid
    assert "draft_sections --> validate_sections" in mermaid
    assert "validate_sections --> consistency_check" in mermaid
    assert "consistency_check --> section_boundary" in mermaid
    assert "section_boundary --> __end__" in mermaid
    assert (
        "apply_outline_decision -. &nbsp;approved&nbsp; .-> outline_boundary"
        not in mermaid
    )
