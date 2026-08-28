"""Agent Usage Service 的离线幂等、预算与权限边界。"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from literature_agent.application.agent_usage_service import (
    AgentUsageError,
    AgentUsageService,
)
from literature_agent.application.ports.agent_usage_control import (
    ToolCallReservationRequest,
)
from literature_agent.domain.agent_usage import (
    AgentToolCallStatus,
    create_agent_turn_usage,
)
from literature_agent.domain.research_agent import (
    create_agent_session,
    create_agent_turn_run,
    create_context_snapshot,
    create_project_research_workspace_policy_snapshot,
)
from literature_agent.domain.run import RunStatus, RunType, create_run

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


class _Session:
    async def commit(self) -> None: ...
    async def flush(self) -> None: ...
    async def rollback(self) -> None: ...


class _RunRepo:
    def __init__(self, run) -> None:
        self.run = run

    async def get_by_id_for_update(self, run_id: str, owner_id: str):
        return self.run if (run_id, owner_id) == (self.run.run_id, self.run.owner_id) else None

    async def update_status(self, run_id, expected_status, new_status, new_event_sequence):
        if (
            run_id != self.run.run_id
            or expected_status is not self.run.status
            or new_status is not self.run.status
        ):
            return False
        self.run = replace(self.run, event_sequence=new_event_sequence)
        return True


class _EventRepo:
    def __init__(self) -> None:
        self.items = []

    async def add(self, value):
        self.items.append(value)
        return value


class _AgentRepo:
    def __init__(self, *, turn, session, context, policy) -> None:
        self.turn = turn
        self.session = session
        self.context = context
        self.policy = policy

    async def get_turn_scoped(self, run_id: str, owner_id: str):
        return self.turn if run_id == self.turn.turn_run_id and owner_id == "owner-1" else None

    async def get_session_scoped(self, session_id: str, owner_id: str):
        return (
            self.session
            if session_id == self.session.session_id and owner_id == "owner-1"
            else None
        )

    async def get_context_snapshot(self, snapshot_id: str):
        return self.context if snapshot_id == self.context.snapshot_id else None

    async def get_policy_snapshot(self, snapshot_id: str):
        return self.policy if snapshot_id == self.policy.snapshot_id else None


class _UsageRepo:
    def __init__(self, usage) -> None:
        self.usage = usage
        self.models = {}
        self.tools = {}

    async def get_usage(self, turn_run_id):
        return self.usage if turn_run_id == self.usage.turn_run_id else None

    async def get_usage_for_update(self, turn_run_id):
        return await self.get_usage(turn_run_id)

    async def save_usage(self, value):
        self.usage = value

    async def get_model_call(self, key):
        return self.models.get(key)

    async def add_model_call(self, value):
        self.models[value.reservation_key] = value
        return value

    async def save_model_call(self, value):
        self.models[value.reservation_key] = value

    async def get_tool_call(self, key):
        return self.tools.get(key)

    async def add_tool_call(self, value):
        self.tools[value.reservation_key] = value
        return value

    async def list_tool_calls(self, turn_run_id):
        return [v for v in self.tools.values() if v.turn_run_id == turn_run_id]

    async def count_tool_calls_by_signature(self, turn_run_id, tool_name, args_hash):
        return sum(
            value.turn_run_id == turn_run_id
            and value.tool_name == tool_name
            and value.args_hash == args_hash
            for value in self.tools.values()
        )

    async def save_tool_call(self, value, *, expected_status):
        current = self.tools.get(value.reservation_key)
        if current is None or current.status is not expected_status:
            return False
        self.tools[value.reservation_key] = value
        return True


def _scenario(
    *,
    with_events: bool = False,
    run_status: RunStatus = RunStatus.RUNNING,
):
    run = replace(
        create_run("project-1", "owner-1", RunType.AGENT_TURN),
        status=run_status,
    )
    session = create_agent_session(owner_id="owner-1", project_id="project-1", title=None)
    message_id = "message-1"
    context = create_context_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id=session.session_id,
        turn_run_id=run.run_id,
        user_message_id=message_id,
        history_through_sequence=1,
        review_output_id="review-1",
    )
    policy = create_project_research_workspace_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id=session.session_id,
        turn_run_id=run.run_id,
    )
    turn = create_agent_turn_run(
        turn_run_id=run.run_id,
        session_id=session.session_id,
        user_message_id=message_id,
        context_snapshot_id=context.snapshot_id,
        policy_snapshot_id=policy.snapshot_id,
    )
    usage = create_agent_turn_usage(
        turn_run_id=run.run_id,
        owner_id="owner-1",
        project_id="project-1",
        session_id=session.session_id,
        policy_snapshot_id=policy.snapshot_id,
        max_model_calls=policy.max_model_calls,
        max_tool_calls=policy.max_tool_calls,
    )
    usage_repo = _UsageRepo(usage)
    agent_repo = _AgentRepo(turn=turn, session=session, context=context, policy=policy)
    current = [_NOW]

    @asynccontextmanager
    async def session_factory():
        yield _Session()

    run_repo = _RunRepo(run)
    event_repo = _EventRepo()
    service = AgentUsageService(
        session_factory=session_factory,
        run_repo_factory=lambda _: run_repo,
        agent_repo_factory=lambda _: agent_repo,
        usage_repo_factory=lambda _: usage_repo,
        event_repo_factory=(lambda _: event_repo) if with_events else None,
        clock=lambda: current[0],
    )
    return service, usage_repo, agent_repo, current, run.run_id, event_repo


async def test_model_reservation_is_replay_safe_and_input_limit_is_hard() -> None:
    service, repo, _, _, run_id, _ = _scenario()

    first = await service.reserve_model_call(run_id, 1, approximate_input_tokens=59_999)
    replay = await service.reserve_model_call(run_id, 1, approximate_input_tokens=59_999)
    assert first.model_calls_reserved == replay.model_calls_reserved == 1

    with pytest.raises(AgentUsageError, match="Token") as exc_info:
        await service.reserve_model_call(run_id, 2, approximate_input_tokens=60_001)
    assert exc_info.value.code == "agent_input_token_budget_exceeded"
    assert repo.usage.model_calls_reserved == 1


async def test_tool_contract_repeat_and_effect_claim_fail_closed() -> None:
    service, repo, agent_repo, _, run_id, _ = _scenario()
    ref = next(
        value for value in agent_repo.policy.tool_refs if value.name == "search_project_chunks"
    )

    def request(invocation_id: str, *, schema_hash: str = ref.input_schema_hash):
        return ToolCallReservationRequest(
            invocation_id=invocation_id,
            tool_name=ref.name,
            input_schema_hash=schema_hash,
            args_hash="b" * 64,
            input_size_bytes=20,
        )

    first = await service.reserve_tool_call(run_id, request("call-1"))
    assert (await service.reserve_tool_call(run_id, request("call-1"))) == first
    await service.reserve_tool_call(run_id, request("call-2"))
    with pytest.raises(AgentUsageError) as repeated:
        await service.reserve_tool_call(run_id, request("call-3"))
    assert repeated.value.code == "agent_tool_loop_rejected"
    with pytest.raises(AgentUsageError) as drift:
        await service.reserve_tool_call(run_id, request("call-drift", schema_hash="f" * 64))
    assert drift.value.code == "agent_tool_schema_drift"

    running = await service.start_tool_call(run_id, first.reservation_key)
    assert running.status is AgentToolCallStatus.RUNNING
    with pytest.raises(AgentUsageError) as claimed:
        await service.start_tool_call(run_id, first.reservation_key)
    assert claimed.value.code == "agent_tool_effect_in_progress"
    assert repo.usage.tool_calls_reserved == 2


async def test_deadline_starts_at_runtime_and_is_not_reset_by_retry() -> None:
    service, repo, _, current, run_id, _ = _scenario()
    started = await service.start_turn(run_id)
    current[0] += timedelta(seconds=300)

    with pytest.raises(AgentUsageError) as expired:
        await service.reserve_model_call(run_id, 1, approximate_input_tokens=10)
    assert expired.value.code == "agent_turn_deadline_exceeded"
    assert repo.usage.deadline_at == started.deadline_at


async def test_new_reservations_emit_safe_budget_events_but_replay_does_not() -> None:
    service, _, agent_repo, _, run_id, events = _scenario(with_events=True)
    ref = next(
        value for value in agent_repo.policy.tool_refs if value.name == "search_project_chunks"
    )

    await service.reserve_model_call(run_id, 1, approximate_input_tokens=100)
    await service.reserve_model_call(run_id, 1, approximate_input_tokens=100)
    request = ToolCallReservationRequest(
        invocation_id="call-1",
        tool_name=ref.name,
        input_schema_hash=ref.input_schema_hash,
        args_hash="b" * 64,
        input_size_bytes=20,
    )
    await service.reserve_tool_call(run_id, request)
    await service.reserve_tool_call(run_id, request)

    assert [event.event_type for event in events.items] == [
        "agent_budget_updated",
        "agent_budget_updated",
    ]
    assert all(
        set(event.payload)
        == {
            "model_calls_reserved",
            "max_model_calls",
            "tool_calls_reserved",
            "max_tool_calls",
        }
        for event in events.items
    )


async def test_cancel_requested_rejects_new_model_and_tool_reservations() -> None:
    service, repo, agent_repo, _, run_id, _ = _scenario(
        run_status=RunStatus.CANCEL_REQUESTED
    )
    ref = next(
        value for value in agent_repo.policy.tool_refs if value.name == "search_project_chunks"
    )

    with pytest.raises(AgentUsageError) as model_rejected:
        await service.reserve_model_call(run_id, 1, approximate_input_tokens=10)
    with pytest.raises(AgentUsageError) as tool_rejected:
        await service.reserve_tool_call(
            run_id,
            ToolCallReservationRequest(
                invocation_id="cancelled-call",
                tool_name=ref.name,
                input_schema_hash=ref.input_schema_hash,
                args_hash="a" * 64,
                input_size_bytes=2,
            ),
        )

    assert model_rejected.value.code == tool_rejected.value.code == "agent_turn_cancelled"
    assert repo.usage.model_calls_reserved == repo.usage.tool_calls_reserved == 0


async def test_application_enforces_model_and_tool_call_maxima() -> None:
    service, repo, agent_repo, _, run_id, _ = _scenario()
    ref = next(value for value in agent_repo.policy.tool_refs if value.name == "write_file")

    for ordinal in range(1, repo.usage.max_model_calls + 1):
        await service.reserve_model_call(run_id, ordinal, approximate_input_tokens=10)
    with pytest.raises(AgentUsageError) as model_limit:
        await service.reserve_model_call(
            run_id, repo.usage.max_model_calls + 1, approximate_input_tokens=10
        )

    for ordinal in range(1, repo.usage.max_tool_calls + 1):
        await service.reserve_tool_call(
            run_id,
            ToolCallReservationRequest(
                invocation_id=f"tool-{ordinal}",
                tool_name=ref.name,
                input_schema_hash=ref.input_schema_hash,
                args_hash=f"{ordinal:064x}",
                input_size_bytes=ordinal,
            ),
        )
    with pytest.raises(AgentUsageError) as tool_limit:
        await service.reserve_tool_call(
            run_id,
            ToolCallReservationRequest(
                invocation_id="tool-over-limit",
                tool_name=ref.name,
                input_schema_hash=ref.input_schema_hash,
                args_hash="f" * 64,
                input_size_bytes=1,
            ),
        )

    assert model_limit.value.code == "agent_model_budget_exceeded"
    assert tool_limit.value.code == "agent_tool_budget_exceeded"
    assert repo.usage.model_calls_reserved == repo.usage.max_model_calls
    assert repo.usage.tool_calls_reserved == repo.usage.max_tool_calls


async def test_model_usage_is_recorded_in_two_phases_and_conflicts_fail_closed() -> None:
    service, repo, _, _, run_id, _ = _scenario()
    await service.reserve_model_call(run_id, 1, approximate_input_tokens=123)

    await service.record_model_usage(run_id, 1, input_tokens=120, output_tokens=None)
    await service.record_model_usage(run_id, 1, input_tokens=None, output_tokens=45)
    await service.record_model_usage(run_id, 1, input_tokens=120, output_tokens=45)

    reservation = repo.models[f"model:{run_id}:1"]
    assert (reservation.input_tokens, reservation.output_tokens) == (120, 45)
    assert (repo.usage.input_tokens, repo.usage.output_tokens) == (120, 45)
    with pytest.raises(AgentUsageError) as conflict:
        await service.record_model_usage(run_id, 1, input_tokens=120, output_tokens=46)
    assert conflict.value.code == "agent_model_usage_conflict"


async def test_tool_terminal_replay_conflicts_and_64_kib_limit_fail_closed() -> None:
    service, repo, agent_repo, _, run_id, _ = _scenario()
    ref = next(
        value for value in agent_repo.policy.tool_refs if value.name == "search_project_chunks"
    )

    def request(invocation_id: str, args_hash: str) -> ToolCallReservationRequest:
        return ToolCallReservationRequest(
            invocation_id=invocation_id,
            tool_name=ref.name,
            input_schema_hash=ref.input_schema_hash,
            args_hash=args_hash,
            input_size_bytes=10,
        )

    succeeded = await service.reserve_tool_call(run_id, request("success", "1" * 64))
    await service.start_tool_call(run_id, succeeded.reservation_key)
    first_success = await service.succeed_tool_call(
        run_id,
        succeeded.reservation_key,
        output_size_bytes=64 * 1024,
        result_hash="a" * 64,
    )
    assert (
        await service.succeed_tool_call(
            run_id,
            succeeded.reservation_key,
            output_size_bytes=64 * 1024,
            result_hash="a" * 64,
        )
    ) == first_success
    with pytest.raises(AgentUsageError) as success_conflict:
        await service.succeed_tool_call(
            run_id,
            succeeded.reservation_key,
            output_size_bytes=64 * 1024,
            result_hash="b" * 64,
        )

    failed = await service.reserve_tool_call(run_id, request("failure", "2" * 64))
    await service.start_tool_call(run_id, failed.reservation_key)
    first_failure = await service.fail_tool_call(
        run_id,
        failed.reservation_key,
        error_code="safe_failure",
        safe_message="Tool 调用失败",
    )
    assert (
        await service.fail_tool_call(
            run_id,
            failed.reservation_key,
            error_code="safe_failure",
            safe_message="Tool 调用失败",
        )
    ) == first_failure
    with pytest.raises(AgentUsageError) as failure_conflict:
        await service.fail_tool_call(
            run_id,
            failed.reservation_key,
            error_code="different_failure",
            safe_message="Tool 调用失败",
        )

    oversized = await service.reserve_tool_call(run_id, request("oversized", "3" * 64))
    await service.start_tool_call(run_id, oversized.reservation_key)
    with pytest.raises(AgentUsageError) as output_limit:
        await service.succeed_tool_call(
            run_id,
            oversized.reservation_key,
            output_size_bytes=64 * 1024 + 1,
            result_hash="c" * 64,
        )

    assert success_conflict.value.code == "agent_tool_result_conflict"
    assert failure_conflict.value.code == "agent_tool_error_conflict"
    assert output_limit.value.code == "agent_tool_output_too_large"
    assert repo.tools[oversized.reservation_key].status is AgentToolCallStatus.RUNNING
