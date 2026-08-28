"""在 Runtime 调用前将当轮冻结附件物化到 Sandbox inbox。"""

import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.ports.agent_attachment_inbox import AgentAttachmentInbox
from literature_agent.application.ports.agent_attachment_repository import (
    AgentAttachmentRepository,
)
from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.research_agent_runtime import RuntimeTurnRequest
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.agent_attachment import (
    agent_attachment_inbox_path,
    validate_agent_attachment_content,
)
from literature_agent.domain.run import RunStatus

TSession = TypeVar("TSession", bound=Session)


class AgentAttachmentMaterializationError(Exception):
    """附件授权、内容或 Sandbox fence 失效，必须在模型前关闭失败。"""


class AgentAttachmentMaterializer[TSession: Session]:
    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
        attachment_repo_factory: Callable[[TSession], AgentAttachmentRepository],
        storage: Storage,
    ) -> None:
        self._session_factory = session_factory
        self._agent_repo_factory = agent_repo_factory
        self._run_repo_factory = run_repo_factory
        self._attachment_repo_factory = attachment_repo_factory
        self._storage = storage

    async def materialize(
        self, request: RuntimeTurnRequest, inbox: AgentAttachmentInbox
    ) -> None:
        attachments = await self._load_current(request)
        await inbox.assert_current()
        await inbox.reset()
        for ref, attachment in zip(
            request.context_snapshot.attachment_refs, attachments, strict=True
        ):
            content = await self._storage.read(attachment.storage_key)
            validated = validate_agent_attachment_content(
                display_name=ref.display_name,
                media_type=ref.media_type,
                content=content,
            )
            if (
                validated.content_hash != ref.content_hash
                or validated.size_bytes != ref.size_bytes
                or hashlib.sha256(content).hexdigest() != attachment.content_hash
            ):
                raise AgentAttachmentMaterializationError("attachment_content_drift")
            await self._assert_current(request)
            await inbox.assert_current()
            await inbox.upload(
                agent_attachment_inbox_path(ref.attachment_id, ref.display_name), content
            )
        await self._assert_current(request)
        await inbox.assert_current()

    async def _load_current(self, request: RuntimeTurnRequest):
        await self._assert_current(request)
        async with self._session_factory() as db:
            values = await self._attachment_repo_factory(
                db
            ).get_many_available_scoped(
                tuple(ref.attachment_id for ref in request.context_snapshot.attachment_refs),
                request.session_id,
                request.context_snapshot.owner_id,
            )
            if len(values) != len(request.context_snapshot.attachment_refs):
                raise AgentAttachmentMaterializationError("attachment_scope_invalid")
            for ref, value in zip(
                request.context_snapshot.attachment_refs, values, strict=True
            ):
                if (
                    value.project_id != request.context_snapshot.project_id
                    or value.version != ref.version
                    or value.content_hash != ref.content_hash
                    or value.size_bytes != ref.size_bytes
                    or value.media_type != ref.media_type
                    or value.display_name != ref.display_name
                ):
                    raise AgentAttachmentMaterializationError("attachment_fact_drift")
            return values

    async def _assert_current(self, request: RuntimeTurnRequest) -> None:
        async with self._session_factory() as db:
            session = await self._agent_repo_factory(db).get_session_scoped(
                request.session_id, request.context_snapshot.owner_id
            )
            run = await self._run_repo_factory(db).get_by_id(request.turn_run_id)
            if (
                session is None
                or session.project_id != request.context_snapshot.project_id
                or session.active_turn_run_id != request.turn_run_id
                or run is None
                or run.owner_id != request.context_snapshot.owner_id
                or run.project_id != request.context_snapshot.project_id
                or run.status is not RunStatus.RUNNING
            ):
                raise AgentAttachmentMaterializationError("attachment_turn_not_current")
