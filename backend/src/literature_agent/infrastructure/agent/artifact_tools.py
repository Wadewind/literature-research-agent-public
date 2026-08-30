"""把 fenced Sandbox 文件源与 AgentArtifact 提交包装为 Deep Agents Tool。"""

import asyncio
import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool

from literature_agent.application.agent_artifact_service import (
    AgentArtifactServiceError,
    AgentArtifactSubmissionService,
)
from literature_agent.application.ports.agent_artifact_source import (
    AgentArtifactSourceScope,
)
from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeTurnRequest,
)
from literature_agent.application.runtime_execution_control import (
    RuntimeExecutionControlError,
)
from literature_agent.domain.agent_artifact import (
    AgentArtifactMediaType,
    AgentArtifactValidationError,
)
from literature_agent.infrastructure.agent.sandbox_workspace import (
    SandboxLeaseStatus,
    SandboxWorkspaceLease,
    SandboxWorkspaceRepository,
)


class SandboxAgentArtifactSource:
    """在 Adapter 内把当前 Lease 变成 SDK-neutral 普通文件源。"""

    def __init__(
        self,
        lease: SandboxWorkspaceLease,
        repository: SandboxWorkspaceRepository,
    ) -> None:
        self._lease = lease
        self._repository = repository
        record = lease.record
        self._scope = AgentArtifactSourceScope(
            owner_id=record.owner_id,
            project_id=record.project_id,
            session_id=record.session_id,
            turn_run_id=record.holder_turn_run_id,
            sandbox_generation=record.generation,
            sandbox_fencing_token=record.fencing_token,
        )

    @property
    def scope(self) -> AgentArtifactSourceScope:
        return self._scope

    async def assert_current(self) -> None:
        current = await self._repository.get_lease(self._scope.session_id)
        if (
            current is None
            or current != self._lease.record
            or current.status is not SandboxLeaseStatus.ACTIVE
        ):
            raise AgentArtifactServiceError(
                "artifact_sandbox_fence_lost",
                "Artifact Sandbox generation/fence 已失效",
            )

    async def read_regular_file(self, path: str, *, max_bytes: int) -> bytes:
        entries = await asyncio.to_thread(self._lease.backend.list_workspace_files)
        matches = [item for item in entries if item[0] == path]
        if len(matches) != 1:
            raise AgentArtifactValidationError(
                "artifact_file_not_found", "Artifact 文件不存在或不是唯一普通文件"
            )
        _path, entry_type, declared_size = matches[0]
        if str(entry_type).lower().rsplit(".", 1)[-1] != "file":
            raise AgentArtifactValidationError(
                "artifact_file_not_regular", "Artifact 只允许普通文件"
            )
        if declared_size < 0 or declared_size > max_bytes:
            raise AgentArtifactValidationError("artifact_too_large", "Artifact 文件超过 10 MiB")
        responses = await asyncio.to_thread(self._lease.backend.download_files, [path])
        if (
            len(responses) != 1
            or responses[0].path != path
            or responses[0].error is not None
            or responses[0].content is None
        ):
            raise AgentArtifactServiceError(
                "artifact_download_failed", "Artifact 文件读取失败", temporary=True
            )
        content = responses[0].content
        if len(content) != declared_size:
            raise AgentArtifactValidationError(
                "artifact_size_mismatch", "Artifact 声明大小与实际内容不一致"
            )
        return content


class AgentArtifactToolFactory:
    """每个真实 Sandbox Turn 绑定 request、lease 与稳定 Tool call ID。"""

    def __init__(
        self,
        service: AgentArtifactSubmissionService[Any],
        workspace_repository: SandboxWorkspaceRepository,
    ) -> None:
        self._service = service
        self._workspace_repository = workspace_repository

    def create(
        self, request: RuntimeTurnRequest, lease: SandboxWorkspaceLease
    ) -> tuple[BaseTool, ...]:
        service = self._service
        source = SandboxAgentArtifactSource(lease, self._workspace_repository)

        @tool
        async def submit_artifact(
            path: str,
            name: str,
            media_type: AgentArtifactMediaType,
            runtime: ToolRuntime[Any],
            source_url: str | None = None,
        ) -> str:
            """提交 outputs 成果。

            支持 PNG/JPEG/SVG/PDF/CSV/Markdown/TXT/JSON/Python (`text/x-python`)；
            网络来源可附带 HTTP(S) source_url。
            """
            permit = getattr(runtime.context, "runtime_permit", None)
            context_turn_run_id = getattr(runtime.context, "turn_run_id", None)
            if (
                permit is None
                or context_turn_run_id != request.turn_run_id
                or not runtime.tool_call_id
            ):
                raise ResearchAgentRuntimeError(
                    kind=RuntimeErrorKind.PERMANENT,
                    code="artifact_runtime_context_invalid",
                    safe_message="Artifact Runtime 上下文无效",
                )
            declared_media_type = (
                media_type.value if isinstance(media_type, AgentArtifactMediaType) else media_type
            )
            try:
                candidate = await service.submit(
                    request=request,
                    permit=permit,
                    source=source,
                    tool_call_id=runtime.tool_call_id,
                    path=path,
                    name=name,
                    media_type=declared_media_type,
                    source_url=source_url,
                )
            except RuntimeExecutionControlError as exc:
                raise ResearchAgentRuntimeError(
                    kind=(
                        RuntimeErrorKind.CANCELLED
                        if exc.code == "runtime_turn_cancelled"
                        else RuntimeErrorKind.TEMPORARY
                        if exc.temporary
                        else RuntimeErrorKind.PERMANENT
                    ),
                    code=exc.code,
                    safe_message=exc.safe_message,
                ) from exc
            except AgentArtifactServiceError as exc:
                raise ResearchAgentRuntimeError(
                    kind=(
                        RuntimeErrorKind.TEMPORARY if exc.temporary else RuntimeErrorKind.PERMANENT
                    ),
                    code=exc.code,
                    safe_message=exc.safe_message,
                ) from exc
            except Exception as exc:
                raise ResearchAgentRuntimeError(
                    kind=RuntimeErrorKind.TEMPORARY,
                    code="artifact_submission_failed",
                    safe_message="Artifact 提交暂时失败",
                ) from exc
            return json.dumps(
                {
                    "candidate_id": candidate.candidate_id,
                    "status": "validated_not_published",
                    "name": candidate.name,
                    "media_type": candidate.media_type,
                    "content_hash": candidate.content_hash,
                    "size_bytes": candidate.size_bytes,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        return (submit_artifact,)
