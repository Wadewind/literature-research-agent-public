"""Agent Runtime 结果正文与 Evidence DTO 的双重契约。"""

import pytest

from literature_agent.application.agent_turn_executor import AgentTurnExecutor
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
)


def test_runtime_result_rejects_body_and_dto_evidence_mismatch() -> None:
    with pytest.raises(ResearchAgentRuntimeError) as exc_info:
        AgentTurnExecutor._parse_runtime_answer(  # noqa: SLF001
            "结论 [evidence:evidence-body]",
            ("evidence-dto",),
        )

    assert exc_info.value.code == "runtime_evidence_mismatch"
