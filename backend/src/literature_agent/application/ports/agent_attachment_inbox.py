"""Agent 输入附件物化的 SDK-neutral Sandbox inbox 端口。"""

from typing import Protocol


class AgentAttachmentInbox(Protocol):
    async def assert_current(self) -> None: ...

    async def reset(self) -> None: ...

    async def upload(self, path: str, content: bytes) -> None: ...
