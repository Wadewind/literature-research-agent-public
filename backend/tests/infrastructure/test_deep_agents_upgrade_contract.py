"""Deep Agents 固定版本升级前置契约。"""

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntime,
)
from literature_agent.infrastructure.agent.deep_agents_upgrade_contract import (
    PINNED_DEEPAGENTS_VERSION,
    REQUIRED_CREATE_DEEP_AGENT_PARAMETERS,
    REQUIRED_RUNTIME_PORT_METHODS,
    verify_deep_agents_upgrade_contract,
)


def test_pinned_deep_agents_public_assembly_surface_is_available() -> None:
    result = verify_deep_agents_upgrade_contract()

    assert PINNED_DEEPAGENTS_VERSION == "0.7.8"
    assert result.installed_version == PINNED_DEEPAGENTS_VERSION
    assert result.missing_create_deep_agent_parameters == ()
    assert set(result.available_parameters) >= REQUIRED_CREATE_DEEP_AGENT_PARAMETERS


def test_runtime_port_remains_sdk_neutral_and_minimal() -> None:
    public_methods = {
        name
        for name, value in ResearchAgentRuntime.__dict__.items()
        if callable(value) and not name.startswith("_")
    }

    assert public_methods == REQUIRED_RUNTIME_PORT_METHODS
    assert not any("deep" in name.lower() or "langgraph" in name.lower() for name in public_methods)
