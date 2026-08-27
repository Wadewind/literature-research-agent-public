"""在 AgentTurn 成功短事务中发布正式 AgentArtifact。"""

from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.domain.agent_artifact import AgentArtifact, create_agent_artifact
from literature_agent.domain.research_agent import AgentArtifactCandidateStatus


class RepositoryAgentArtifactPublisher:
    """不执行文件 I/O，只以 Candidate CAS 与唯一约束收敛发布。"""

    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    async def publish_for_success(
        self,
        *,
        owner_id: str,
        project_id: str,
        session_id: str,
        turn_run_id: str,
    ) -> tuple[AgentArtifact, ...]:
        turn = await self._repository.get_turn_scoped(turn_run_id, owner_id)
        session = await self._repository.get_session_scoped(session_id, owner_id)
        if (
            turn is None
            or session is None
            or turn.session_id != session_id
            or session.project_id != project_id
            or session.active_turn_run_id != turn_run_id
        ):
            raise ValueError("agent_artifact_publish_scope_invalid")

        existing_artifacts = {
            value.candidate_id: value
            for value in await self._repository.list_artifacts_scoped(turn_run_id, owner_id)
        }
        published: list[AgentArtifact] = []
        for candidate in await self._repository.list_candidates_scoped(turn_run_id, owner_id):
            if (
                candidate.project_id != project_id
                or candidate.session_id != session_id
                or candidate.turn_run_id != turn_run_id
            ):
                raise ValueError("agent_artifact_publish_scope_invalid")
            if candidate.status is AgentArtifactCandidateStatus.COMMITTED:
                artifact = existing_artifacts.get(candidate.candidate_id)
                if artifact is None:
                    raise ValueError("committed_candidate_missing_artifact")
                published.append(artifact)
                continue
            if candidate.status is not AgentArtifactCandidateStatus.VALIDATED:
                continue
            if not await self._repository.is_sandbox_fence_current(
                owner_id=owner_id,
                project_id=project_id,
                session_id=session_id,
                turn_run_id=turn_run_id,
                sandbox_generation=candidate.sandbox_generation or 0,
                sandbox_fencing_token=candidate.sandbox_fencing_token or 0,
            ):
                raise ValueError("agent_artifact_sandbox_fence_lost")
            committed = candidate.commit()
            if not await self._repository.save_candidate(
                committed, expected_status=AgentArtifactCandidateStatus.VALIDATED.value
            ):
                current = await self._repository.get_candidate(candidate.candidate_id)
                if current is None or current.status is not AgentArtifactCandidateStatus.COMMITTED:
                    raise ValueError("agent_artifact_candidate_cas_failed")
                committed = current
            artifact = await self._repository.add_artifact_if_absent(
                create_agent_artifact(candidate=committed)
            )
            published.append(artifact)
        return tuple(published)
