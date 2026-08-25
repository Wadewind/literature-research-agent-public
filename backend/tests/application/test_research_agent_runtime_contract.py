"""ResearchAgentRuntime Port 的 SDK 隔离契约测试。"""

import inspect
from typing import get_type_hints

from literature_agent.application.ports.research_agent_runtime import ResearchAgentRuntime


def test_runtime_port_is_small_and_uses_only_project_types() -> None:
    """Port 只保留执行、恢复、取消、对账和收集五个业务操作。"""
    public_methods = {
        name
        for name, member in inspect.getmembers(ResearchAgentRuntime, inspect.isfunction)
        if not name.startswith("_")
    }

    assert public_methods == {
        "execute_turn",
        "resume_turn",
        "cancel_turn",
        "reconcile_turn",
        "collect_turn_result",
    }


def test_runtime_port_annotations_do_not_leak_sdk_types() -> None:
    """Port 注解不得引用 Deep Agents 或 LangGraph 类型。"""
    forbidden_fragments = ("deepagents", "deep_agents", "langgraph", "langchain")

    for method_name in (
        "execute_turn",
        "resume_turn",
        "cancel_turn",
        "reconcile_turn",
        "collect_turn_result",
    ):
        hints = get_type_hints(getattr(ResearchAgentRuntime, method_name))
        annotation_text = " ".join(str(value).lower() for value in hints.values())
        assert not any(fragment in annotation_text for fragment in forbidden_fragments)
