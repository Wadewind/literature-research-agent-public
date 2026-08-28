"""public-egress 业务事实的离线 ORM 契约。"""

from literature_agent.infrastructure.persistence.models import (
    AgentArtifactCandidateORM,
    AgentArtifactORM,
    AgentPolicySnapshotORM,
    AgentSandboxLeaseORM,
)


def test_policy_and_lease_freeze_complete_network_profile_reference() -> None:
    for table in (
        AgentPolicySnapshotORM.__table__,
        AgentSandboxLeaseORM.__table__,
    ):
        assert "network_profile_id" in table.columns
        assert "network_profile_version" in table.columns
        assert "network_profile_hash" in table.columns
        assert table.c.network_profile_hash.type.length == 64

    policy_constraints = {item.name for item in AgentPolicySnapshotORM.__table__.constraints}
    lease_constraints = {item.name for item in AgentSandboxLeaseORM.__table__.constraints}
    assert "ck_agent_policy_network_profile" in policy_constraints
    assert "ck_agent_sandbox_lease_network_profile" in lease_constraints

    for table in (AgentArtifactCandidateORM.__table__, AgentArtifactORM.__table__):
        assert table.c.source_url.type.length == 2048
        assert table.c.source_url_hash.type.length == 64
