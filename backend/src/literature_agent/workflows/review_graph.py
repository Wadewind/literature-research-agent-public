"""固定 Review Workflow 的 LangGraph 运行时骨架。"""

from collections.abc import Awaitable, Callable
from typing import NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import EmptyInputError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
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
    feedback_human_input_id: NotRequired[str]
    human_input_request_id: NotRequired[str]
    human_input_id: NotRequired[str]
    outline_action: NotRequired[str]
    outline_boundary_reached: NotRequired[bool]
    claim_set_id: NotRequired[str]
    consistency_output_id: NotRequired[str]
    final_output_id: NotRequired[str]
    final_artifact_id: NotRequired[str]
    section_boundary_reached: NotRequired[bool]


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


async def _review_outline_interrupt(state: ReviewGraphState) -> dict:
    """纯 Interrupt 节点；恢复值只携带待业务库复核的稳定 ID。"""
    request_id = state.get("human_input_request_id")
    outline_output_id = state.get("outline_output_id")
    if not request_id or not outline_output_id:
        raise CheckpointDataError("大纲 Interrupt 缺少持久 Request/Output ID")
    resumed = interrupt(
        {
            "kind": "review_outline",
            "request_id": request_id,
            "outline_output_id": outline_output_id,
        }
    )
    if (
        not isinstance(resumed, dict)
        or set(resumed) != {"request_id", "human_input_id"}
        or not isinstance(resumed["request_id"], str)
        or not resumed["request_id"]
        or not isinstance(resumed["human_input_id"], str)
        or not resumed["human_input_id"]
    ):
        raise CheckpointDataError("HumanInput Resume Command 结构非法")
    return {
        "human_input_request_id": resumed["request_id"],
        "human_input_id": resumed["human_input_id"],
    }


async def _outline_route(state: ReviewGraphState) -> str:
    action = state.get("outline_action")
    if action == "feedback":
        return "feedback"
    if action in {"approve", "edit"}:
        return "approved"
    raise CheckpointDataError("持久 HumanInput action 非法")


async def _outline_boundary(_state: ReviewGraphState) -> dict:
    """切片 8 接入章节写作前的安全占位边界。"""
    return {"outline_boundary_reached": True}


async def _section_boundary(_state: ReviewGraphState) -> dict:
    """切片 9 接入 Artifact 导出前的安全占位边界。"""
    return {"section_boundary_reached": True}


class ReviewGraphFactory:
    """构建 ``review.v1`` 固定图的可演进骨架。

    当前只允许注入一个切片节点，用于验证 checkpoint 与节点幂等恢复；Evidence、
    Outline 和 HITL 节点由后续切片按固定顺序替换。本工厂尚不注册到生产 Worker。
    """

    def __init__(
        self,
        slice_node: ReviewGraphNode | None = None,
        *,
        outline_entry_node: ReviewGraphNode | None = None,
        outline_decision_node: ReviewGraphNode | None = None,
        section_draft_node: ReviewGraphNode | None = None,
        section_validate_node: ReviewGraphNode | None = None,
        consistency_node: ReviewGraphNode | None = None,
        export_node: ReviewGraphNode | None = None,
        finalize_node: ReviewGraphNode | None = None,
    ) -> None:
        self._slice_node = slice_node
        self._outline_entry_node = outline_entry_node
        self._outline_decision_node = outline_decision_node
        self._section_draft_node = section_draft_node
        self._section_validate_node = section_validate_node
        self._consistency_node = consistency_node
        self._export_node = export_node
        self._finalize_node = finalize_node
        if slice_node is None and (outline_entry_node is None or outline_decision_node is None):
            raise ValueError("必须提供 slice_node 或完整 Outline 节点")
        if slice_node is not None and (
            outline_entry_node is not None or outline_decision_node is not None
        ):
            raise ValueError("切片骨架与 Outline 图不能同时配置")
        configured = (section_draft_node, section_validate_node, consistency_node)
        if any(item is not None for item in configured) and not all(
            item is not None for item in configured
        ):
            raise ValueError("章节图必须同时提供 draft/validate/consistency 节点")
        if slice_node is not None and any(item is not None for item in configured):
            raise ValueError("切片骨架与章节图不能同时配置")
        if (export_node is None) != (finalize_node is None):
            raise ValueError("导出图必须同时提供 export/finalize 节点")
        if export_node is not None and not all(item is not None for item in configured):
            raise ValueError("导出图依赖完整章节图")

    def compile(self, checkpointer: BaseCheckpointSaver):
        """使用调用方持有生命周期的持久 Checkpointer 编译图。"""
        graph = StateGraph(ReviewGraphState)
        if self._slice_node is not None:
            graph.add_node("slice_boundary", RunnableLambda(self._slice_node))
            graph.add_edge(START, "slice_boundary")
            graph.add_edge("slice_boundary", END)
            return graph.compile(checkpointer=checkpointer)
        outline_entry_node = self._outline_entry_node
        outline_decision_node = self._outline_decision_node
        if outline_entry_node is None or outline_decision_node is None:  # pragma: no cover
            raise ValueError("Outline 图节点配置不完整")
        graph.add_node("propose_outline", RunnableLambda(outline_entry_node))
        graph.add_node("review_outline", _review_outline_interrupt)
        graph.add_node("apply_outline_decision", RunnableLambda(outline_decision_node))
        section_draft_node = self._section_draft_node
        section_validate_node = self._section_validate_node
        consistency_node = self._consistency_node
        if (
            section_draft_node is not None
            and section_validate_node is not None
            and consistency_node is not None
        ):
            graph.add_node("draft_sections", RunnableLambda(section_draft_node))
            graph.add_node("validate_sections", RunnableLambda(section_validate_node))
            graph.add_node("consistency_check", RunnableLambda(consistency_node))
            if self._export_node is not None and self._finalize_node is not None:
                graph.add_node("export_review", RunnableLambda(self._export_node))
                graph.add_node("finalize", RunnableLambda(self._finalize_node))
            else:
                graph.add_node("section_boundary", RunnableLambda(_section_boundary))
            approved_target = "draft_sections"
        else:
            approved_target = "outline_boundary"
            graph.add_node("outline_boundary", RunnableLambda(_outline_boundary))
        graph.add_edge(START, "propose_outline")
        graph.add_edge("propose_outline", "review_outline")
        graph.add_edge("review_outline", "apply_outline_decision")
        graph.add_conditional_edges(
            "apply_outline_decision",
            _outline_route,
            {"feedback": "propose_outline", "approved": approved_target},
        )
        if section_draft_node is not None:
            graph.add_edge("draft_sections", "validate_sections")
            graph.add_edge("validate_sections", "consistency_check")
            if self._export_node is not None:
                graph.add_edge("consistency_check", "export_review")
                graph.add_edge("export_review", "finalize")
                graph.add_edge("finalize", END)
            else:
                graph.add_edge("consistency_check", "section_boundary")
                graph.add_edge("section_boundary", END)
        else:
            graph.add_edge("outline_boundary", END)
        return graph.compile(checkpointer=checkpointer)


class ReviewWorkflowRuntime:
    """隔离 LangGraph 类型和错误分类的 Review Runtime 边界。"""

    def __init__(self, graph_factory: ReviewGraphFactory, checkpointer: BaseCheckpointSaver):
        self._checkpointer = checkpointer
        self._graph = graph_factory.compile(checkpointer)

    async def has_checkpoint(self, review_run_id: str) -> bool:
        """只在明确不存在 checkpoint 时允许执行器创建首个图输入。"""
        try:
            return (
                await self._checkpointer.aget_tuple(review_graph_config(review_run_id)) is not None
            )
        except PsycopgError as exc:
            raise CheckpointUnavailableError("Checkpoint 数据库暂时不可用") from exc
        except Exception as exc:
            raise CheckpointDataError("Checkpoint 无法安全读取") from exc

    async def start(self, state: ReviewGraphState) -> ReviewGraphState:
        """仅用于首次执行；完整初始 State 不能作为恢复输入重复提交。"""
        if state["workflow_version"] != WORKFLOW_VERSION:
            raise CheckpointDataError("Workflow 版本与 review.v1 runtime 不匹配")
        return await self._invoke(state, state["review_run_id"])

    async def resume(self, review_run_id: str) -> ReviewGraphState:
        """从同一 Thread 的 pending checkpoint 恢复，不创建新一轮图输入。"""
        return await self._invoke(None, review_run_id)

    async def resume_human_input(
        self,
        review_run_id: str,
        *,
        request_id: str,
        human_input_id: str,
    ) -> ReviewGraphState:
        """用已持久化 HumanInput 的稳定 ID 恢复同一 Interrupt。"""
        if not request_id or not human_input_id:
            raise CheckpointDataError("HumanInput Resume 缺少稳定 ID")
        return await self._invoke(
            Command(
                resume={
                    "request_id": request_id,
                    "human_input_id": human_input_id,
                }
            ),
            review_run_id,
        )

    async def _invoke(
        self,
        state: ReviewGraphState | Command | None,
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
        if not isinstance(result, dict):
            raise CheckpointDataError("Checkpoint 返回的 Graph State 结构非法")
        return ReviewGraphState(**result)
