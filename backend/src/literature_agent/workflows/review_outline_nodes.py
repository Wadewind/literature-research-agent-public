"""Outline 应用服务到固定 LangGraph Node 的薄适配。"""

from literature_agent.application.review_outline_service import (
    ReviewOutlineDecisionService,
    ReviewOutlineService,
)
from literature_agent.domain.exceptions import CheckpointDataError
from literature_agent.domain.review import HumanInputAction
from literature_agent.workflows.review_graph import ReviewGraphState


class ReviewOutlineGraphNodes:
    """只把 Graph 小型 ID State 映射到持久化 Outline 用例。"""

    def __init__(
        self,
        *,
        owner_id: str,
        outline_service: ReviewOutlineService,
        decision_service: ReviewOutlineDecisionService,
    ) -> None:
        if not owner_id:
            raise ValueError("owner_id 不能为空")
        self._owner_id = owner_id
        self._outline_service = outline_service
        self._decision_service = decision_service

    async def propose(self, state: ReviewGraphState) -> dict:
        """生成或复用当前反馈轮 Outline，并持久化 Request/等待状态。"""
        strategy_id = state.get("search_strategy_output_id")
        matrix_id = state.get("evidence_matrix_output_id")
        feedback_round = state.get("feedback_round", 0)
        if (
            not strategy_id
            or not matrix_id
            or not isinstance(feedback_round, int)
            or isinstance(feedback_round, bool)
            or feedback_round < 0
        ):
            raise CheckpointDataError("Outline Node 缺少稳定输入 ID 或 feedback_round 非法")
        result = await self._outline_service.propose_and_pause(
            run_id=state["review_run_id"],
            project_id=state["project_id"],
            owner_id=self._owner_id,
            search_strategy_output_id=strategy_id,
            evidence_matrix_output_id=matrix_id,
            feedback_round=feedback_round,
            feedback_human_input_id=state.get("feedback_human_input_id"),
            correlation_id=(f"review-outline:{state['review_run_id']}:round:{feedback_round}"),
        )
        return {
            "outline_output_id": result.output.output_id,
            "human_input_request_id": result.request.request_id,
            "feedback_round": feedback_round,
        }

    async def apply_decision(self, state: ReviewGraphState) -> dict:
        """只按 Resume 的稳定 ID 回读持久 HumanInput 并更新小型 Graph State。"""
        request_id = state.get("human_input_request_id")
        human_input_id = state.get("human_input_id")
        if not request_id or not human_input_id:
            raise CheckpointDataError("Outline Decision 缺少持久 HumanInput ID")
        decision = await self._decision_service.load(
            run_id=state["review_run_id"],
            project_id=state["project_id"],
            owner_id=self._owner_id,
            request_id=request_id,
            human_input_id=human_input_id,
        )
        if decision.action is HumanInputAction.FEEDBACK:
            feedback_round = state.get("feedback_round", 0)
            if not isinstance(feedback_round, int) or isinstance(feedback_round, bool):
                raise CheckpointDataError("feedback_round 非法")
            return {
                "outline_action": decision.action.value,
                "feedback_round": feedback_round + 1,
                "feedback_human_input_id": decision.human_input_id,
            }
        return {
            "outline_action": decision.action.value,
            "approved_outline_output_id": decision.approved_outline_output_id,
        }
