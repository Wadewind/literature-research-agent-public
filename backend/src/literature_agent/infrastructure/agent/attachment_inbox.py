"""SandboxWorkspaceLease 的受控附件 inbox Adapter。"""

import asyncio

from literature_agent.application.agent_attachment_materializer import (
    AgentAttachmentMaterializer,
)
from literature_agent.application.ports.agent_attachment_inbox import AgentAttachmentInbox
from literature_agent.application.ports.research_agent_runtime import RuntimeTurnRequest
from literature_agent.domain.agent_attachment import (
    AGENT_ATTACHMENT_INBOX_ROOT,
    is_agent_attachment_inbox_path,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
    SandboxWorkspaceRepository,
)


class FencedSandboxAttachmentInbox(AgentAttachmentInbox):
    def __init__(
        self, lease: SandboxWorkspaceLease, repository: SandboxWorkspaceRepository
    ) -> None:
        self._lease = lease
        self._repository = repository

    async def assert_current(self) -> None:
        expected = self._lease.record
        current = await self._repository.get_lease(expected.session_id)
        if (
            current is None
            or current.status is not SandboxLeaseStatus.ACTIVE
            or current.generation != expected.generation
            or current.fencing_token != expected.fencing_token
            or current.holder_turn_run_id != expected.holder_turn_run_id
        ):
            raise RuntimeError("Sandbox attachment inbox fence 已失权")

    async def reset(self) -> None:
        response = await asyncio.to_thread(
            self._lease.backend.execute,
            "rm -rf -- /workspace/inbox && mkdir -m 700 -- /workspace/inbox",
        )
        if response.exit_code != 0:
            raise RuntimeError("Sandbox attachment inbox 重置失败")

    async def upload(self, path: str, content: bytes) -> None:
        if not is_agent_attachment_inbox_path(path) or path == AGENT_ATTACHMENT_INBOX_ROOT:
            raise ValueError("Sandbox attachment inbox 路径非法")
        responses = await asyncio.to_thread(
            self._lease.backend.upload_files, [(path, content)]
        )
        if (
            len(responses) != 1
            or responses[0].path != path
            or responses[0].error is not None
        ):
            raise RuntimeError("Sandbox attachment inbox 上传失败")


class SandboxRuntimeAttachmentMaterializer:
    """把 Application Materializer 绑定到当前 fenced Lease。"""

    def __init__(
        self,
        service: AgentAttachmentMaterializer,
        repository: SandboxWorkspaceRepository,
    ) -> None:
        self._service = service
        self._repository = repository

    async def materialize(
        self, request: RuntimeTurnRequest, lease: SandboxWorkspaceLease
    ) -> None:
        await self._service.materialize(
            request, FencedSandboxAttachmentInbox(lease, self._repository)
        )
