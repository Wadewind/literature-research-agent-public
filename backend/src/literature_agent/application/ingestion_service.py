"""文献导入应用服务。"""

import hashlib
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TypeVar

from literature_agent.application.ports.event_repository import EventRepository
from literature_agent.application.ports.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRepository,
)
from literature_agent.application.ports.paper_repository import PaperRepository
from literature_agent.application.ports.paper_version_repository import (
    PaperVersionRepository,
)
from literature_agent.application.ports.project_repository import ProjectRepository
from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.event import create_event
from literature_agent.domain.exceptions import (
    FileValidationError,
    IdempotencyConflictError,
    ProjectNotFoundError,
    RunNotFoundError,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.run import Run, create_run

TSession = TypeVar("TSession", bound=Session)

_PDF_MAGIC = b"%PDF-"
_IDEMPOTENCY_KEY_MAX_LENGTH = 255


@dataclass(frozen=True, slots=True)
class UploadResult:
    """上传接口返回结果。"""

    run_id: str
    paper_id: str
    version_id: str
    status: str


def _compute_sha256(content: bytes) -> str:
    """计算字节内容的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(content).hexdigest()


def _compute_request_hash(
    project_id: str,
    idempotency_key: str,
    file_hash: str,
    filename: str,
    content_type: str,
) -> str:
    """计算请求指纹，用于幂等冲突检测。"""
    payload = f"{project_id}:{idempotency_key}:{file_hash}:{filename}:{content_type}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sanitize_filename(filename: str) -> str:
    """清理文件名，仅保留安全字符用作展示信息。

    不用于生成存储路径，因此不需要保持完整语义。
    """
    name = filename.strip().replace(" ", "_")
    name = re.sub(r"[^\w.\-]", "_", name)
    name = re.sub(r"_{2,}", "_", name)
    if not name or name in {".", ".."}:
        return "upload.pdf"
    return name[:255]


class IngestionService:
    """文献导入用例层，负责上传校验、存储、幂等和 Run 创建。"""

    def __init__(
        self,
        max_upload_size_bytes: int,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        project_repo_factory: Callable[[TSession], ProjectRepository],
        paper_repo_factory: Callable[[TSession], PaperRepository],
        paper_version_repo_factory: Callable[[TSession], PaperVersionRepository],
        idempotency_repo_factory: Callable[[TSession], IdempotencyRepository],
        run_repo_factory: Callable[[TSession], RunRepository],
        event_repo_factory: Callable[[TSession], EventRepository],
        storage: Storage,
    ) -> None:
        """初始化 IngestionService。

        参数:
            max_upload_size_bytes: 允许的最大上传字节数。
            session_factory: 返回异步上下文管理器的工厂，用于控制事务。
            project_repo_factory: 根据 session 创建 ProjectRepository 的工厂。
            paper_repo_factory: 根据 session 创建 PaperRepository 的工厂。
            paper_version_repo_factory: 根据 session 创建 PaperVersionRepository 的工厂。
            idempotency_repo_factory: 根据 session 创建 IdempotencyRepository 的工厂。
            run_repo_factory: 根据 session 创建 RunRepository 的工厂。
            event_repo_factory: 根据 session 创建 EventRepository 的工厂。
            storage: 文件存储适配器。
        """
        self._max_upload_size_bytes = max_upload_size_bytes
        self._session_factory = session_factory
        self._project_repo_factory = project_repo_factory
        self._paper_repo_factory = paper_repo_factory
        self._paper_version_repo_factory = paper_version_repo_factory
        self._idempotency_repo_factory = idempotency_repo_factory
        self._run_repo_factory = run_repo_factory
        self._event_repo_factory = event_repo_factory
        self._storage = storage

    async def upload_paper_file(
        self,
        actor: ActorContext,
        project_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        idempotency_key: str,
        correlation_id: str,
    ) -> UploadResult:
        """上传 PDF 并创建 Ingestion Run。

        参数:
            actor: 当前请求的可信用户上下文。
            project_id: 目标 Project 标识符。
            filename: 原始文件名，仅用于展示。
            content_type: 文件 MIME 类型。
            content: 文件字节内容。
            idempotency_key: 调用方提供的幂等键。
            correlation_id: 关联标识符。

        返回:
            上传结果，包含 run_id、paper_id、version_id 和初始状态。

        异常:
            FileValidationError: 文件校验失败。
            IdempotencyConflictError: 幂等键冲突。
            ProjectNotFoundError: Project 不存在或不属于当前 actor。
        """
        self._validate_upload(idempotency_key, content)
        sanitized_filename = _sanitize_filename(filename)
        file_hash = _compute_sha256(content)
        request_hash = _compute_request_hash(
            project_id,
            idempotency_key,
            file_hash,
            sanitized_filename,
            content_type,
        )

        async with self._session_factory() as session:
            idempotency_repo = self._idempotency_repo_factory(session)
            existing = await idempotency_repo.get(actor.owner_id, idempotency_key)
            run_repo = self._run_repo_factory(session)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError(idempotency_key)
                # 命中幂等缓存，直接返回已创建的 Run 信息
                return await self._result_from_run(run_repo, existing.run_id)

            project_repo = self._project_repo_factory(session)
            project = await project_repo.get_by_id(project_id)
            if project is None or project.owner_id != actor.owner_id:
                raise ProjectNotFoundError(project_id)

            paper = create_paper(actor.owner_id, project_id)
            storage_key = self._build_storage_key(actor.owner_id, project_id, paper.paper_id)
            version = create_paper_version(
                paper_id=paper.paper_id,
                file_hash=file_hash,
                storage_key=storage_key,
                size_bytes=len(content),
                content_type=content_type,
            )

            run = create_run(
                project_id=project_id,
                owner_id=actor.owner_id,
                run_type="ingestion",
                input_payload={
                    "paper_id": paper.paper_id,
                    "version_id": version.version_id,
                    "filename": sanitized_filename,
                    "content_type": content_type,
                    "file_hash": file_hash,
                },
            )
            created_event = create_event(
                run_id=run.run_id,
                sequence=1,
                event_type="run_created",
                actor_type="user",
                correlation_id=correlation_id,
                payload={"status": run.status.value},
            )
            updated_run = self._with_event_sequence(run, 2)

            await self._paper_repo_factory(session).add(paper)
            await self._paper_version_repo_factory(session).add(version)
            await self._run_repo_factory(session).add(updated_run)
            await session.flush()
            await self._event_repo_factory(session).add(created_event)
            await self._storage.write(storage_key, content)

            record = IdempotencyRecord(
                owner_id=actor.owner_id,
                idempotency_key=idempotency_key,
                project_id=project_id,
                request_hash=request_hash,
                run_id=updated_run.run_id,
            )
            try:
                await idempotency_repo.add(record)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        return UploadResult(
            run_id=updated_run.run_id,
            paper_id=paper.paper_id,
            version_id=version.version_id,
            status=updated_run.status.value,
        )

    def _validate_upload(self, idempotency_key: str, content: bytes) -> None:
        """校验上传请求和文件内容。"""
        if not idempotency_key or len(idempotency_key) > _IDEMPOTENCY_KEY_MAX_LENGTH:
            raise FileValidationError("Idempotency-Key 不能为空且长度不得超过 255")
        if len(content) > self._max_upload_size_bytes:
            raise FileValidationError(f"文件大小超过限制 {self._max_upload_size_bytes} 字节")
        if not content.startswith(_PDF_MAGIC):
            raise FileValidationError("仅接受 PDF 文件")

    def _build_storage_key(self, owner_id: str, project_id: str, paper_id: str) -> str:
        """生成文件在 Storage 中的键。"""
        return f"{owner_id}/{project_id}/{paper_id}/paper.pdf"

    def _with_event_sequence(self, run: Run, sequence: int) -> Run:
        """返回 event_sequence 更新后的 Run 实体。"""
        return Run(
            run_id=run.run_id,
            project_id=run.project_id,
            owner_id=run.owner_id,
            run_type=run.run_type,
            status=run.status,
            input_payload=run.input_payload,
            result_payload=run.result_payload,
            event_sequence=sequence,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    async def _result_from_run(
        self,
        run_repo: RunRepository,
        run_id: str,
    ) -> UploadResult:
        """从已有 Run 中组装 UploadResult。"""
        run = await run_repo.get_by_id(run_id)
        if run is None:
            # 幂等记录存在但 Run 不存在属于不一致状态，按未找到处理
            raise RunNotFoundError(run_id)
        payload = run.input_payload
        return UploadResult(
            run_id=run.run_id,
            paper_id=payload.get("paper_id", ""),
            version_id=payload.get("version_id", ""),
            status=run.status.value,
        )
