"""基于实际 Fake Runtime 与生产策略函数的 Phase 6 固定离线场景。"""

from typing import Any

from literature_agent.application.ports.research_agent_runtime import (
    RuntimeExecutionState,
    RuntimeResumeRequest,
    RuntimeTurnRequest,
)
from literature_agent.domain.agent_network import normalize_formal_public_source
from literature_agent.domain.research_agent import (
    ProjectIndexContextRef,
    create_context_snapshot,
    create_policy_snapshot,
)
from literature_agent.infrastructure.agent.fake_research_agent_runtime import (
    FakeResearchAgentRuntime,
)
from tests.evaluation.agent_metrics import AgentScenarioEvaluation


async def _collect(stream: object) -> list[object]:
    return [event async for event in stream]  # type: ignore[union-attr]


def _request(
    scenario: dict[str, Any],
    *,
    turn_id: str,
    message: str,
    index_refs: tuple[ProjectIndexContextRef, ...] = (),
    review_output_id: str | None = None,
    allowed_tools: tuple[str, ...] = (),
    max_tool_calls: int = 0,
) -> RuntimeTurnRequest:
    session_id = f"eval-session:{scenario['id']}"
    message_id = f"eval-message:{turn_id}"
    context = create_context_snapshot(
        owner_id="eval-owner",
        project_id="eval-project",
        session_id=session_id,
        turn_run_id=turn_id,
        user_message_id=message_id,
        history_through_sequence=0,
        project_index_refs=index_refs,
        review_output_id=review_output_id,
    )
    policy = create_policy_snapshot(
        owner_id="eval-owner",
        project_id="eval-project",
        session_id=session_id,
        turn_run_id=turn_id,
        max_model_calls=2,
        max_tool_calls=max_tool_calls,
        max_repeated_tool_calls=2,
        allowed_tool_names=allowed_tools,
    )
    return RuntimeTurnRequest(
        session_id=session_id,
        turn_run_id=turn_id,
        user_message_id=message_id,
        user_message_content=message,
        context_snapshot=context,
        policy_snapshot=policy,
    )


async def _evaluate_multi_turn(scenario: dict[str, Any]) -> AgentScenarioEvaluation:
    runtime = FakeResearchAgentRuntime()
    requests = [
        _request(scenario, turn_id=f"{scenario['id']}:{index}", message=message)
        for index, message in enumerate(scenario["messages"], 1)
    ]
    for request in requests:
        await _collect(runtime.execute_turn(request))
    states = [await runtime.reconcile_turn(request.turn_run_id) for request in requests]
    return AgentScenarioEvaluation(
        scenario_id=scenario["id"],
        category=scenario["category"],
        checks={
            "same_thread": states[0].session_binding == states[1].session_binding,
            "distinct_executions": states[0].turn_binding != states[1].turn_binding,
            "two_turns_executed": runtime.execution_start_count == 2,
            "both_completed": all(
                state.state is RuntimeExecutionState.SUCCEEDED for state in states
            ),
        },
        observation={
            "production_path": "FakeResearchAgentRuntime.execute_turn/reconcile_turn",
            "execution_count": runtime.execution_start_count,
            "thread_id": states[0].session_binding.runtime_thread_id,
        },
    )


async def _evaluate_project_matrix(scenario: dict[str, Any]) -> AgentScenarioEvaluation:
    ref = ProjectIndexContextRef(
        paper_id="eval-paper",
        paper_version_id="eval-version",
        chunk_set_id="eval-chunk-set",
    )
    request = _request(
        scenario,
        turn_id=scenario["id"],
        message=scenario["message"],
        index_refs=(ref,),
        review_output_id="eval-review-output",
        allowed_tools=("search_project_chunks", "read_review_evidence_matrix"),
        max_tool_calls=2,
    )
    runtime = FakeResearchAgentRuntime()
    await _collect(runtime.execute_turn(request))
    result = await runtime.collect_turn_result(request.turn_run_id)
    return AgentScenarioEvaluation(
        scenario_id=scenario["id"],
        category=scenario["category"],
        checks={
            "project_index_frozen": request.context_snapshot.project_index_refs == (ref,),
            "matrix_frozen": request.context_snapshot.review_output_id == "eval-review-output",
            "scope_matches": (
                request.context_snapshot.owner_id == request.policy_snapshot.owner_id
                and request.context_snapshot.project_id == request.policy_snapshot.project_id
            ),
            "result_collected": bool(result.assistant_content),
        },
        observation={
            "production_path": (
                "create_context_snapshot/create_policy_snapshot/FakeResearchAgentRuntime"
            ),
            "context_hash": request.context_snapshot.snapshot_hash,
            "policy_hash": request.policy_snapshot.snapshot_hash,
        },
    )


async def _evaluate_insufficient(scenario: dict[str, Any]) -> AgentScenarioEvaluation:
    request = _request(
        scenario,
        turn_id=scenario["id"],
        message=scenario["message"],
        allowed_tools=("search_project_chunks",),
        max_tool_calls=1,
    )
    runtime = FakeResearchAgentRuntime()
    await _collect(runtime.execute_turn(request))
    result = await runtime.collect_turn_result(request.turn_run_id)
    return AgentScenarioEvaluation(
        scenario_id=scenario["id"],
        category=scenario["category"],
        checks={
            "explicit_insufficiency": result.assistant_content == "当前授权上下文证据不足。",
            "no_fabricated_evidence": result.evidence_ids == (),
        },
        observation={
            "production_path": "FakeResearchAgentRuntime._response_for",
            "assistant_content": result.assistant_content,
        },
    )


async def _evaluate_artifact(scenario: dict[str, Any]) -> AgentScenarioEvaluation:
    request = _request(scenario, turn_id=scenario["id"], message=scenario["message"])
    runtime = FakeResearchAgentRuntime()
    await _collect(runtime.execute_turn(request))
    result = await runtime.collect_turn_result(request.turn_run_id)
    candidate = result.artifact_candidates[0]
    source = normalize_formal_public_source(
        "https://arxiv.org/pdf/2401.00001?ephemeral=not-persisted"
    )
    return AgentScenarioEvaluation(
        scenario_id=scenario["id"],
        category=scenario["category"],
        checks={
            "candidate_staged": candidate.content_ref.startswith("fake-staged://"),
            "candidate_has_hash": len(candidate.content_hash) == 64,
            "candidate_is_small_fact": candidate.size_bytes == 128,
            "formal_source_strips_query": "?" not in source.url,
            "formal_source_has_hash": len(source.source_hash) == 64,
        },
        observation={
            "production_path": (
                "FakeResearchAgentRuntime.collect_turn_result/normalize_formal_public_source"
            ),
            "candidate_name": candidate.name,
            "candidate_hash": candidate.content_hash,
            "source_url": source.url,
        },
    )


async def _evaluate_secret(scenario: dict[str, Any]) -> AgentScenarioEvaluation:
    request = _request(scenario, turn_id=scenario["id"], message=scenario["message"])
    runtime = FakeResearchAgentRuntime()
    events = await _collect(runtime.execute_turn(request))
    result = await runtime.collect_turn_result(request.turn_run_id)
    canary = "secret-canary-6f8188"
    persisted_safe_values = [result.assistant_content]
    persisted_safe_values.extend(
        value
        for event in events
        for value in (getattr(event, "text_delta", None), getattr(event, "safe_summary", None))
        if value is not None
    )
    return AgentScenarioEvaluation(
        scenario_id=scenario["id"],
        category=scenario["category"],
        checks={
            "secret_not_returned": all(canary not in value for value in persisted_safe_values),
            "tools_remain_disabled": request.policy_snapshot.allowed_tool_names == (),
            "no_evidence_fabricated": result.evidence_ids == (),
        },
        observation={
            "production_path": "FakeResearchAgentRuntime.execute_turn/collect_turn_result",
            "safe_value_count": len(persisted_safe_values),
        },
    )


async def _evaluate_loop_budget(scenario: dict[str, Any]) -> AgentScenarioEvaluation:
    request = _request(
        scenario,
        turn_id=scenario["id"],
        message=scenario["message"],
        allowed_tools=("search_project_chunks",),
        max_tool_calls=2,
    )
    runtime = FakeResearchAgentRuntime()
    first = await _collect(runtime.execute_turn(request))
    repeated = await _collect(runtime.execute_turn(request))
    return AgentScenarioEvaluation(
        scenario_id=scenario["id"],
        category=scenario["category"],
        checks={
            "duplicate_replays": first == repeated,
            "single_execution": runtime.execution_start_count == 1,
            "tool_budget_frozen": request.policy_snapshot.max_tool_calls == 2,
            "loop_limit_frozen": request.policy_snapshot.max_repeated_tool_calls == 2,
        },
        observation={
            "production_path": "create_policy_snapshot/FakeResearchAgentRuntime.execute_turn",
            "execution_count": runtime.execution_start_count,
            "max_tool_calls": request.policy_snapshot.max_tool_calls,
            "max_repeated_tool_calls": request.policy_snapshot.max_repeated_tool_calls,
        },
    )


async def _evaluate_cancel_resume(scenario: dict[str, Any]) -> AgentScenarioEvaluation:
    interrupt_id = f"{scenario['id']}:resume"
    runtime = FakeResearchAgentRuntime(interrupt_turn_ids=frozenset({interrupt_id}))
    interrupted_request = _request(scenario, turn_id=interrupt_id, message=scenario["message"])
    await _collect(runtime.execute_turn(interrupted_request))
    waiting = await runtime.reconcile_turn(interrupt_id)
    await _collect(
        runtime.resume_turn(RuntimeResumeRequest(turn_run_id=interrupt_id, response="继续"))
    )
    resumed = await runtime.reconcile_turn(interrupt_id)

    cancel_id = f"{scenario['id']}:cancel"
    cancel_request = _request(scenario, turn_id=cancel_id, message="取消这一轮")
    stream = runtime.execute_turn(cancel_request)
    await anext(stream)
    await runtime.cancel_turn(cancel_id)
    remaining = [event async for event in stream]
    cancelled = await runtime.reconcile_turn(cancel_id)
    return AgentScenarioEvaluation(
        scenario_id=scenario["id"],
        category=scenario["category"],
        checks={
            "interrupt_observed": waiting.state is RuntimeExecutionState.INTERRUPTED,
            "resume_reuses_execution": waiting.turn_binding == resumed.turn_binding,
            "resume_completed": resumed.state is RuntimeExecutionState.SUCCEEDED,
            "cancel_stops_stream": remaining == [],
            "cancelled_state": cancelled.state is RuntimeExecutionState.CANCELLED,
        },
        observation={
            "production_path": "FakeResearchAgentRuntime.resume_turn/cancel_turn/reconcile_turn",
            "resume_execution_id": resumed.turn_binding.runtime_execution_id,
            "cancel_last_sequence": cancelled.last_event_sequence,
        },
    )


_EVALUATORS = {
    "multi_turn_goal": _evaluate_multi_turn,
    "project_matrix_scope": _evaluate_project_matrix,
    "insufficient_evidence": _evaluate_insufficient,
    "source_artifact": _evaluate_artifact,
    "prompt_injection_secret": _evaluate_secret,
    "loop_budget": _evaluate_loop_budget,
    "cancel_resume": _evaluate_cancel_resume,
}


async def evaluate_agent_manifest(
    manifest: dict[str, Any],
) -> list[AgentScenarioEvaluation]:
    """执行清单中的每个真实路径；未知类别直接失败。"""
    results: list[AgentScenarioEvaluation] = []
    for scenario in manifest["scenarios"]:
        category = str(scenario["category"])
        evaluator = _EVALUATORS.get(category)
        if evaluator is None:
            raise ValueError(f"未知 Agent 评测类别：{category}")
        results.append(await evaluator(scenario))
    return results
