"""Conversation / Message 领域实体（切片 8）。

Conversation 绑定 Project，scope 只有 ``project`` / ``selected_papers``
两值且创建后不可修改；``active_run_id`` 是会话级单活跃 Run 的认领字段
（条件更新认领，终态清理）。Message 在 Conversation 内按 ``sequence``
严格递增；user 消息关联其 rag_answer Run，assistant 消息关联产生它的
Run 与 ClaimSet。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

# 提问内容字符上限（2026-08-21 定稿）
MESSAGE_CONTENT_MAX_LENGTH = 4000
# Conversation 标题字符上限
CONVERSATION_TITLE_MAX_LENGTH = 200
# 未显式命名时，取首条问题前 50 字符作为标题
TITLE_FROM_FIRST_MESSAGE_CHARS = 50


class ScopeMode(StrEnum):
    """Conversation 检索范围模式（创建后不可修改）。"""

    PROJECT = "project"
    SELECTED_PAPERS = "selected_papers"


class MessageRole(StrEnum):
    """Message 角色。"""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Conversation:
    """一个 Project 内的问答会话。

    属性:
        conversation_id: 会话标识符。
        project_id: 所属 Project。
        owner_id: 所有者标识符。
        title: 会话标题；未命名时为 None，首条问题提交时回填。
        scope_mode: 检索范围模式（不可修改）。
        active_run_id: 当前活跃 rag_answer Run；None 表示空闲。
        created_at: 创建时间（UTC）。
    """

    conversation_id: str
    project_id: str
    owner_id: str
    title: str | None
    scope_mode: ScopeMode
    active_run_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ConversationScopePaper:
    """``selected_papers`` 模式创建时解析固化的默认范围条目。

    属性:
        conversation_id: 所属会话。
        paper_id: 范围内的 Paper。
        version_id: 创建时解析出的 PaperVersion（固化，不随收录换版变化）。
    """

    conversation_id: str
    paper_id: str
    version_id: str


@dataclass(frozen=True, slots=True)
class Message:
    """会话内的一条消息。

    属性:
        message_id: 消息标识符。
        conversation_id: 所属会话。
        sequence: 会话内严格递增顺序，从 1 开始。
        role: 角色（user / assistant）。
        content: 消息文本。
        run_id: 关联的 rag_answer Run（user 消息关联其触发的 Run，
            assistant 消息关联产生它的 Run）；无关 Run 时为 None。
        claim_set_id: 仅 assistant 消息，关联的 ClaimSet。
        created_at: 创建时间（UTC）。
    """

    message_id: str
    conversation_id: str
    sequence: int
    role: MessageRole
    content: str
    run_id: str | None
    claim_set_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def create_conversation(
    *,
    project_id: str,
    owner_id: str,
    title: str | None,
    scope_mode: ScopeMode,
) -> Conversation:
    """创建新的 Conversation。

    异常:
        ValueError: 标题超过长度上限。
    """
    if title is not None:
        title = title.strip() or None
        if title is not None and len(title) > CONVERSATION_TITLE_MAX_LENGTH:
            raise ValueError(
                f"会话标题长度不能超过 {CONVERSATION_TITLE_MAX_LENGTH}"
            )
    return Conversation(
        conversation_id=str(uuid4()),
        project_id=project_id,
        owner_id=owner_id,
        title=title,
        scope_mode=scope_mode,
        active_run_id=None,
        created_at=datetime.now(UTC),
    )


def create_scope_paper(
    conversation_id: str,
    paper_id: str,
    version_id: str,
) -> ConversationScopePaper:
    """创建一条固化的默认范围条目。"""
    return ConversationScopePaper(
        conversation_id=conversation_id,
        paper_id=paper_id,
        version_id=version_id,
    )


def create_message(
    *,
    conversation_id: str,
    sequence: int,
    role: MessageRole,
    content: str,
    run_id: str | None = None,
    claim_set_id: str | None = None,
) -> Message:
    """创建一条新 Message。

    异常:
        ValueError: 内容为空或超过长度上限，或 sequence 小于 1。
    """
    if not content.strip():
        raise ValueError("消息内容不能为空")
    if len(content) > MESSAGE_CONTENT_MAX_LENGTH:
        raise ValueError(f"消息内容长度不能超过 {MESSAGE_CONTENT_MAX_LENGTH}")
    if sequence < 1:
        raise ValueError(f"消息 sequence 必须 >= 1: {sequence}")
    return Message(
        message_id=str(uuid4()),
        conversation_id=conversation_id,
        sequence=sequence,
        role=role,
        content=content,
        run_id=run_id,
        claim_set_id=claim_set_id,
        created_at=datetime.now(UTC),
    )


def derive_title(content: str) -> str:
    """从首条问题内容派生会话标题（前 50 字符，去除首尾空白）。"""
    return content.strip()[:TITLE_FROM_FIRST_MESSAGE_CHARS]
