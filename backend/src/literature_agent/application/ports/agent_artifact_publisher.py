"""Agent Artifact 成功提交端口。"""

from typing import Protocol

from literature_agent.domain.agent_artifact import AgentArtifact


class AgentArtifactPublisher(Protocol):
    """只在业务 Turn 成功短事务中发布已校验 Candidate。"""

    async def publish_for_success(
        self,
        *,
        owner_id: str,
        project_id: str,
        session_id: str,
        turn_run_id: str,
    ) -> tuple[AgentArtifact, ...]: ...
