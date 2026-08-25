"""Deep Agents Adapter 使用真实 PostgreSQL Checkpointer 的恢复证据。"""

from langchain_core.tools import tool
from sqlalchemy import text

from literature_agent.application.ports.research_agent_runtime import RuntimeExecutionState
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    DeepAgentsResearchAgentRuntime,
)
from literature_agent.infrastructure.workflow.postgres_checkpoint import (
    PostgresCheckpointStore,
)
from tests.fakes.deep_agent_model import ScriptedDeepAgentChatModel
from tests.infrastructure.test_deep_agents_research_agent_runtime import (
    _collect,
    _request,
)


async def test_postgres_checkpoint_recovers_two_turn_thread_across_adapter_instances(
    db_engine,
) -> None:
    """新连接/新 Adapter 仅凭 turn_run_id 对账成功结果，且不重放模型或 Tool。"""
    database_url = db_engine.url.render_as_string(hide_password=False)
    store = PostgresCheckpointStore(database_url)
    tool_calls: list[str] = []

    @tool
    def record_research_step(note: str) -> str:
        """记录确定性且仅进程内可观察的测试副作用。"""
        tool_calls.append(note)
        return "recorded"

    first_model = ScriptedDeepAgentChatModel()
    first_request = _request()
    second_request = _request(turn_run_id="turn-2")
    async with store.open() as saver:
        await saver.setup()
        first_runtime = DeepAgentsResearchAgentRuntime(
            model=first_model,
            tools=(record_research_step,),
            checkpointer=saver,
            summarization_trigger=("messages", 3),
            summarization_keep=("messages", 1),
        )
        await _collect(first_runtime.execute_turn(first_request))
        await _collect(first_runtime.execute_turn(second_request))
        before_restart = await first_runtime.reconcile_turn("turn-2")

    new_adapter_model = ScriptedDeepAgentChatModel()
    async with store.open() as new_saver:
        new_runtime = DeepAgentsResearchAgentRuntime(
            model=new_adapter_model,
            tools=(record_research_step,),
            checkpointer=new_saver,
            summarization_trigger=("messages", 3),
            summarization_keep=("messages", 1),
        )
        recovered_first = await new_runtime.reconcile_turn("turn-1")
        recovered_second = await new_runtime.reconcile_turn("turn-2")
        recovered_result = await new_runtime.collect_turn_result("turn-2")
        replayed = await _collect(new_runtime.execute_turn(second_request))

        latest_tuple = await new_saver.aget_tuple(
            {
                "configurable": {
                    "thread_id": recovered_second.session_binding.runtime_thread_id,
                    "checkpoint_ns": "",
                    "checkpoint_id": recovered_second.turn_binding.runtime_checkpoint_id,
                }
            }
        )
        assert latest_tuple is not None
        latest_state = (await new_runtime._graph.aget_state(latest_tuple.config)).values  # noqa: SLF001

    assert recovered_first.state is RuntimeExecutionState.SUCCEEDED
    assert recovered_second.state is RuntimeExecutionState.SUCCEEDED
    assert recovered_second == before_restart
    assert recovered_first.session_binding == recovered_second.session_binding
    assert (
        recovered_first.turn_binding.runtime_execution_id
        != recovered_second.turn_binding.runtime_execution_id
    )
    assert recovered_result.assistant_content == "第二轮基于同一 Thread 的压缩上下文继续完成。"
    assert replayed[-1].safe_summary == "Deep Agents 已完成"
    assert new_adapter_model.model_call_count == 0
    assert new_adapter_model.summary_call_count == 0
    assert tool_calls == ["第一轮受控记录"]
    assert latest_state["_summarization_event"]["cutoff_index"] > 0
    assert any(path.startswith("/conversation_history/") for path in latest_state["files"])

    async with db_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT DISTINCT thread_id FROM checkpoints "
                    "WHERE metadata @> CAST(:filter AS jsonb) ORDER BY thread_id"
                ),
                {"filter": '{"agent_runtime_session_id":"session-1"}'},
            )
        ).scalars().all()
    assert rows == [recovered_second.session_binding.runtime_thread_id]
