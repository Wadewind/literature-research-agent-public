"""SDK-neutral 的 Project Research Context Tool Port。"""

from dataclasses import dataclass
from typing import Protocol

from literature_agent.domain.tool_execution import ToolErrorKind

SEARCH_PROJECT_CHUNKS = "search_project_chunks"
READ_REVIEW_EVIDENCE_MATRIX = "read_review_evidence_matrix"


class ProjectResearchContextError(Exception):
    """SDK-neutral、仅携带稳定 code 与安全描述的 Tool 边界错误。"""

    def __init__(self, code: str, safe_message: str, kind: ToolErrorKind) -> None:
        self.code = code
        self.safe_message = safe_message
        self.kind = kind
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class ProjectContextToolResult:
    """可交给 Runtime Tool 的有界安全 JSON 结果。"""

    effect_id: str
    tool_name: str
    payload: dict
    result_hash: str


class ProjectResearchContext(Protocol):
    """Deep Agents 仅通过稳定 turn_run_id 使用的平台研究上下文。"""

    async def search_project_chunks(
        self, turn_run_id: str, *, query: str
    ) -> ProjectContextToolResult: ...

    async def read_review_evidence_matrix(
        self, turn_run_id: str
    ) -> ProjectContextToolResult: ...
