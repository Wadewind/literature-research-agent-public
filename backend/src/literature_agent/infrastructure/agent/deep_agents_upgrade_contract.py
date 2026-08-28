"""Deep Agents 升级前只检查项目依赖的公开装配面。"""

import inspect
from dataclasses import dataclass
from importlib.metadata import version

from deepagents import create_deep_agent

PINNED_DEEPAGENTS_VERSION = "0.7.8"
REQUIRED_CREATE_DEEP_AGENT_PARAMETERS = frozenset(
    {
        "model",
        "tools",
        "system_prompt",
        "middleware",
        "subagents",
        "skills",
        "backend",
        "checkpointer",
    }
)
REQUIRED_RUNTIME_PORT_METHODS = frozenset(
    {
        "execute_turn",
        "resume_turn",
        "cancel_turn",
        "reconcile_turn",
        "collect_turn_result",
    }
)


@dataclass(frozen=True, slots=True)
class DeepAgentsUpgradeContractResult:
    installed_version: str
    available_parameters: tuple[str, ...]
    missing_create_deep_agent_parameters: tuple[str, ...]


def verify_deep_agents_upgrade_contract() -> DeepAgentsUpgradeContractResult:
    """读取锁定版本和公开函数签名；缺少项目装配参数时由测试拒绝升级。"""
    available = tuple(inspect.signature(create_deep_agent).parameters)
    missing = tuple(sorted(REQUIRED_CREATE_DEEP_AGENT_PARAMETERS - set(available)))
    return DeepAgentsUpgradeContractResult(
        installed_version=version("deepagents"),
        available_parameters=available,
        missing_create_deep_agent_parameters=missing,
    )
