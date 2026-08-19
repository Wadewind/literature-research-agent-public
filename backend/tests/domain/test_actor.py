"""Actor Context 领域测试。"""

from literature_agent.domain.actor import ActorContext


def test_actor_context_immutable() -> None:
    """ActorContext 应是不可变值对象。"""
    actor = ActorContext(owner_id="user-1")

    assert actor.owner_id == "user-1"
