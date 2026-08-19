"""Event 领域模型测试。"""

from literature_agent.domain.event import create_event


def test_create_event_has_required_fields() -> None:
    """create_event 应创建包含必要字段的 Event。"""
    event = create_event(
        run_id="run-1",
        sequence=1,
        event_type="run_created",
        actor_type="user",
        correlation_id="corr-1",
        payload={"key": "value"},
    )

    assert event.run_id == "run-1"
    assert event.sequence == 1
    assert event.event_version == "1.0"
    assert event.event_type == "run_created"
    assert event.actor_type == "user"
    assert event.correlation_id == "corr-1"
    assert event.payload == {"key": "value"}
    assert event.event_id
    assert event.occurred_at
