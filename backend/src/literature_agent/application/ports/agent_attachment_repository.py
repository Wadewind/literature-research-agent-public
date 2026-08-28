"""Agent 输入附件持久化端口。"""

from dataclasses import dataclass
from typing import Protocol

from literature_agent.domain.agent_attachment import AgentAttachment


@dataclass(frozen=True, slots=True)
class AgentAttachmentInsertResult:
    """返回幂等插入的最终事实及本事务是否赢得插入。"""

    attachment: AgentAttachment
    inserted: bool


class AgentAttachmentRepository(Protocol):
    """保存不可变附件元数据与消息引用关系。"""

    async def get_by_idempotency_key(
        self, owner_id: str, idempotency_key: str
    ) -> AgentAttachment | None: ...

    async def add_if_absent(self, value: AgentAttachment) -> AgentAttachmentInsertResult: ...

    async def list_scoped(self, session_id: str, owner_id: str) -> list[AgentAttachment]: ...

    async def get_scoped(
        self,
        attachment_id: str,
        session_id: str,
        owner_id: str,
        *,
        for_update: bool = False,
    ) -> AgentAttachment | None: ...

    async def get_many_available_scoped(
        self,
        attachment_ids: tuple[str, ...],
        session_id: str,
        owner_id: str,
        *,
        for_update: bool = False,
    ) -> list[AgentAttachment]: ...

    async def is_referenced(self, attachment_id: str) -> bool: ...

    async def mark_deleted(self, value: AgentAttachment) -> bool: ...
