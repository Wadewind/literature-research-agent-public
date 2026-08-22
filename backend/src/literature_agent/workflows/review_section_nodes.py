"""章节应用服务到固定 LangGraph Node 的薄适配。"""

from literature_agent.application.review_section_service import ReviewSectionService
from literature_agent.domain.exceptions import CheckpointDataError
from literature_agent.workflows.review_graph import ReviewGraphState


class ReviewSectionGraphNodes:
    """只在 Graph State 中传递章节 Output、ClaimSet 和报告 ID。"""

    def __init__(self, *, owner_id: str, service: ReviewSectionService) -> None:
        if not owner_id:
            raise ValueError("owner_id 不能为空")
        self._owner_id = owner_id
        self._service = service

    @staticmethod
    def _required_ids(state: ReviewGraphState) -> tuple[str, str]:
        outline_id = state.get("approved_outline_output_id")
        matrix_id = state.get("evidence_matrix_output_id")
        if not outline_id or not matrix_id:
            raise CheckpointDataError("章节 Node 缺少批准 Outline 或 Matrix Output ID")
        return outline_id, matrix_id

    async def draft(self, state: ReviewGraphState) -> dict:
        outline_id, matrix_id = self._required_ids(state)
        result = await self._service.draft_sections(
            run_id=state["review_run_id"],
            project_id=state["project_id"],
            owner_id=self._owner_id,
            approved_outline_output_id=outline_id,
            evidence_matrix_output_id=matrix_id,
            correlation_id=f"review-sections:{state['review_run_id']}",
        )
        return {"section_output_ids": [item.output_id for item in result.outputs]}

    async def validate(self, state: ReviewGraphState) -> dict:
        outline_id, matrix_id = self._required_ids(state)
        output_ids = state.get("section_output_ids")
        if not output_ids:
            raise CheckpointDataError("引用校验 Node 缺少 Section Output ID")
        result = await self._service.validate_sections(
            run_id=state["review_run_id"],
            project_id=state["project_id"],
            owner_id=self._owner_id,
            approved_outline_output_id=outline_id,
            evidence_matrix_output_id=matrix_id,
            section_output_ids=output_ids,
            correlation_id=f"review-citations:{state['review_run_id']}",
        )
        return {"claim_set_id": result.claim_set.claim_set_id}

    async def consistency(self, state: ReviewGraphState) -> dict:
        outline_id, matrix_id = self._required_ids(state)
        output_ids = state.get("section_output_ids")
        claim_set_id = state.get("claim_set_id")
        if not output_ids or not claim_set_id:
            raise CheckpointDataError("一致性 Node 缺少 Section/ClaimSet ID")
        output = await self._service.consistency_check(
            run_id=state["review_run_id"],
            project_id=state["project_id"],
            owner_id=self._owner_id,
            approved_outline_output_id=outline_id,
            evidence_matrix_output_id=matrix_id,
            section_output_ids=output_ids,
            claim_set_id=claim_set_id,
        )
        return {"consistency_output_id": output.output_id}
