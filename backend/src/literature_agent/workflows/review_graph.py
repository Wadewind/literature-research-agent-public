"""固定 Review Workflow 的 LangGraph 运行时骨架。"""

from collections.abc import Awaitable, Callable
from typing import NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import EmptyInputError
from langgraph.graph import END, START, StateGraph
from psycopg import Error as PsycopgError

from literature_agent.domain.exceptions import (
    CheckpointDataError,
    CheckpointUnavailableError,
)

WORKFLOW_VERSION = "review.v1"


class ReviewGraphState(TypedDict):
    """图内的小型状态；正文和模型原始输出必须只保存业务 ID。"""

    review_run_id: str
    project_id: str
    workflow_version: str
    research_question: NotRequired[str]
    search_strategy_output_id: NotRequired[str]
    review_source_ids: NotRequired[list[str]]
    evidence_matrix_output_id: NotRequired[str]
    outline_output_id: NotRequired[str]
    approved_outline_output_id: NotRequired[str]
    section_output_ids: NotRequired[list[str]]
    feedback_round: NotRequired[int]


ReviewGraphNode = Callable[[ReviewGraphState], Awaitable[dict]]


def review_thread_id(review_run_id: str) -> str:
    """把业务 Review Run 映射为稳定、可诊断的 LangGraph thread_id。"""
    if not review_run_id or ":" in review_run_id:
        raise ValueError("review_run_id 不能为空且不能包含冒号")
    return f"{WORKFLOW_VERSION}:review-run:{review_run_id}"


def review_graph_config(review_run_id: str) -> RunnableConfig:
    """构造版本化 Thread 配置；根 namespace 为空并保留给子图。"""
    return {
        "configurable": {
            "thread_id": review_thread_id(review_run_id),
            # LangGraph 根图固定使用空 namespace；该字段留给子图内部命名。
            "checkpoint_ns": "",
        },
        "metadata": {"workflow_version": WORKFLOW_VERSION},
    }


class ReviewGraphFactory:
    """构建 ``review.v1`` 固定图的可演进骨架。

    当前只允许注入一个切片节点，用于验证 checkpoint 与节点幂等恢复；Evidence、
    Outline 和 HITL 节点由后续切片按固定顺序替换。本工厂尚不注册到生产 Worker。
    """

    def __init__(self, slice_node: ReviewGraphNode) -> None:
        self._slice_node = slice_node

    def compile(self, checkpointer: BaseCheckpointSaver):
        """使用调用方持有生命周期的持久 Checkpointer 编译图。"""
        graph = StateGraph(ReviewGraphState)
        graph.add_node("slice_boundary", RunnableLambda(self._slice_node))
        graph.add_edge(START, "slice_boundary")
        graph.add_edge("slice_boundary", END)
        return graph.compile(checkpointer=checkpointer)


class ReviewWorkflowRuntime:
    """隔离 LangGraph 类型和错误分类的 Review Runtime 边界。"""

    def __init__(self, graph_factory: ReviewGraphFactory, checkpointer: BaseCheckpointSaver):
        self._graph = graph_factory.compile(checkpointer)

    async def start(self, state: ReviewGraphState) -> ReviewGraphState:
        """仅用于首次执行；完整初始 State 不能作为恢复输入重复提交。"""
        if state["workflow_version"] != WORKFLOW_VERSION:
            raise CheckpointDataError("Workflow 版本与 review.v1 runtime 不匹配")
        return await self._invoke(state, state["review_run_id"])

    async def resume(self, review_run_id: str) -> ReviewGraphState:
        """从同一 Thread 的 pending checkpoint 恢复，不创建新一轮图输入。"""
        return await self._invoke(None, review_run_id)

    async def _invoke(
        self,
        state: ReviewGraphState | None,
        review_run_id: str,
    ) -> ReviewGraphState:
        """调用 LangGraph 并归一化 checkpoint 读写错误。"""
        try:
            result = await self._graph.ainvoke(
                state,
                config=review_graph_config(review_run_id),
            )
        except EmptyInputError as exc:
            raise CheckpointDataError("Checkpoint 不存在或没有可恢复状态") from exc
        except PsycopgError as exc:
            raise CheckpointUnavailableError("LangGraph checkpoint 数据库不可用") from exc
        if result is None:
            raise CheckpointDataError("Checkpoint 不存在或没有可恢复状态")
        return ReviewGraphState(**result)
