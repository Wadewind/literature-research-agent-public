"""Artifact 导出应用服务到固定 LangGraph Node 的薄适配。"""

from literature_agent.application.review_export_service import ReviewExportService
from literature_agent.domain.exceptions import CheckpointDataError
from literature_agent.workflows.review_graph import ReviewGraphState


class ReviewExportGraphNodes:
    """Graph State 只传递最终 Output/Artifact 稳定 ID。"""

    def __init__(self, *, owner_id: str, service: ReviewExportService) -> None:
        if not owner_id:
            raise ValueError("owner_id 不能为空")
        self._owner_id = owner_id
        self._service = service

    @staticmethod
    def _inputs(state: ReviewGraphState) -> tuple[str, str, list[str], str, str]:
        outline_id = state.get("approved_outline_output_id")
        matrix_id = state.get("evidence_matrix_output_id")
        section_ids = state.get("section_output_ids")
        claim_set_id = state.get("claim_set_id")
        consistency_id = state.get("consistency_output_id")
        if (
            not outline_id
            or not matrix_id
            or not section_ids
            or not claim_set_id
            or not consistency_id
        ):
            raise CheckpointDataError("Artifact Node 缺少持久章节、引用或一致性 ID")
        return outline_id, matrix_id, section_ids, claim_set_id, consistency_id

    async def export(self, state: ReviewGraphState) -> dict:
        outline_id, matrix_id, section_ids, claim_set_id, consistency_id = self._inputs(state)
        result = await self._service.export(
            run_id=state["review_run_id"],
            project_id=state["project_id"],
            owner_id=self._owner_id,
            approved_outline_output_id=outline_id,
            evidence_matrix_output_id=matrix_id,
            section_output_ids=section_ids,
            claim_set_id=claim_set_id,
            consistency_output_id=consistency_id,
            correlation_id=f"review-export:{state['review_run_id']}",
        )
        return {
            "final_output_id": result.final_output.output_id,
            "final_artifact_id": result.markdown_artifact.artifact_id,
        }

    async def finalize(self, state: ReviewGraphState) -> dict:
        final_artifact_id = state.get("final_artifact_id")
        if not final_artifact_id:
            raise CheckpointDataError("Finalize Node 缺少最终 Artifact ID")
        await self._service.finalize(
            run_id=state["review_run_id"],
            project_id=state["project_id"],
            owner_id=self._owner_id,
            final_artifact_id=final_artifact_id,
            correlation_id=f"review-finalize:{state['review_run_id']}",
        )
        return {"final_artifact_id": final_artifact_id}
