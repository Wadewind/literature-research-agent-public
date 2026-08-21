"""Conversation / Message 领域实体测试（切片 8）。"""

import pytest

from literature_agent.domain.conversation import (
    CONVERSATION_TITLE_MAX_LENGTH,
    MESSAGE_CONTENT_MAX_LENGTH,
    MessageRole,
    ScopeMode,
    create_conversation,
    create_message,
    create_scope_paper,
    derive_title,
)


def test_create_conversation_defaults() -> None:
    """创建会话：默认无标题、无活跃 Run、scope 固化。"""
    conversation = create_conversation(
        project_id="proj-1",
        owner_id="user-1",
        title=None,
        scope_mode=ScopeMode.PROJECT,
    )

    assert conversation.conversation_id
    assert conversation.project_id == "proj-1"
    assert conversation.title is None
    assert conversation.scope_mode is ScopeMode.PROJECT
    assert conversation.active_run_id is None


def test_create_conversation_strips_blank_title() -> None:
    """纯空白标题视为未命名（None）。"""
    conversation = create_conversation(
        project_id="proj-1",
        owner_id="user-1",
        title="   ",
        scope_mode=ScopeMode.SELECTED_PAPERS,
    )

    assert conversation.title is None


def test_create_conversation_rejects_overlong_title() -> None:
    """标题超长直接报错。"""
    with pytest.raises(ValueError, match="标题长度"):
        create_conversation(
            project_id="proj-1",
            owner_id="user-1",
            title="x" * (CONVERSATION_TITLE_MAX_LENGTH + 1),
            scope_mode=ScopeMode.PROJECT,
        )


def test_create_scope_paper_fields() -> None:
    """固化范围条目保存 paper_id 与 version_id。"""
    entry = create_scope_paper("conv-1", "paper-1", "version-1")

    assert entry.conversation_id == "conv-1"
    assert entry.paper_id == "paper-1"
    assert entry.version_id == "version-1"


def test_create_message_validates_content_and_sequence() -> None:
    """消息内容非空、有长度上限，sequence 从 1 开始。"""
    with pytest.raises(ValueError, match="不能为空"):
        create_message(
            conversation_id="conv-1",
            sequence=1,
            role=MessageRole.USER,
            content="   ",
        )
    with pytest.raises(ValueError, match="长度"):
        create_message(
            conversation_id="conv-1",
            sequence=1,
            role=MessageRole.USER,
            content="x" * (MESSAGE_CONTENT_MAX_LENGTH + 1),
        )
    with pytest.raises(ValueError, match="sequence"):
        create_message(
            conversation_id="conv-1",
            sequence=0,
            role=MessageRole.USER,
            content="问题",
        )


def test_create_message_fields() -> None:
    """消息保存角色、内容与关联 Run/ClaimSet。"""
    message = create_message(
        conversation_id="conv-1",
        sequence=2,
        role=MessageRole.ASSISTANT,
        content="回答",
        run_id="run-1",
        claim_set_id="cs-1",
    )

    assert message.sequence == 2
    assert message.role is MessageRole.ASSISTANT
    assert message.run_id == "run-1"
    assert message.claim_set_id == "cs-1"


def test_derive_title_truncates_at_50_chars() -> None:
    """未命名会话取首条问题前 50 字符（去首尾空白）。"""
    assert derive_title("  什么是 GNN？  ") == "什么是 GNN？"
    long_content = "问" * 60
    assert derive_title(long_content) == "问" * 50
