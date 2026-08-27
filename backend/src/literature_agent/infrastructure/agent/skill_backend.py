"""把冻结 Skill 版本物化为 Deep Agents 可读、Sandbox 不可见的虚拟文件。"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from deepagents.backends import BackendProtocol
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeTurnRequest,
)
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.skill_repository import SkillRepository
from literature_agent.domain.skill_configuration import SkillSource, SkillVersion


@dataclass(frozen=True, slots=True)
class SkillRuntimeMaterialization:
    backend: BackendProtocol
    sources: tuple[str, ...]


class ReadOnlySkillBackend(BackendProtocol):
    """进程内不可变文本映射；所有修改 API 明确返回 permission denied。"""

    def __init__(self, files: Mapping[str, str]) -> None:
        self._files = dict(files)

    def ls(self, path: str) -> LsResult:
        normalized = path.rstrip("/") + "/"
        entries: dict[str, FileInfo] = {}
        for file_path, content in self._files.items():
            if not file_path.startswith(normalized):
                continue
            relative = file_path[len(normalized) :]
            if "/" in relative:
                directory = normalized + relative.split("/", 1)[0] + "/"
                entries[directory] = FileInfo(path=directory, is_dir=True, size=0, modified_at="")
            else:
                entries[file_path] = FileInfo(
                    path=file_path,
                    is_dir=False,
                    size=len(content.encode()),
                    modified_at="",
                )
        return LsResult(entries=sorted(entries.values(), key=lambda item: item["path"]))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        content = self._files.get(file_path)
        if content is None:
            return ReadResult(error=f"File '{file_path}' not found")
        if limit <= 0:
            return ReadResult(
                file_data={"content": "", "encoding": "utf-8"},
                no_lines_requested=True,
            )
        lines = content.splitlines(keepends=True)
        start = max(offset, 0)
        selected = lines[start : start + limit]
        if not selected:
            return ReadResult(
                file_data={"content": "", "encoding": "utf-8"},
                start_line=max(1, min(start + 1, len(lines) or 1)),
                end_line=max(1, min(start + 1, len(lines) or 1)),
                total_lines=max(len(lines), 1),
            )
        end = start + len(selected)
        return ReadResult(
            file_data={"content": "".join(selected), "encoding": "utf-8"},
            start_line=start + 1,
            end_line=end,
            next_offset=end if end < len(lines) else None,
            total_lines=len(lines),
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        root = (path or "/").rstrip("/") + "/"
        matches: list[GrepMatch] = []
        truncated = False
        for file_path, content in sorted(self._files.items()):
            if not file_path.startswith(root) or (glob and not fnmatch.fnmatch(file_path, glob)):
                continue
            for line_number, line in enumerate(content.splitlines(), 1):
                if pattern not in line:
                    continue
                if max_count is not None and len(matches) >= max_count:
                    truncated = True
                    break
                matches.append(GrepMatch(path=file_path, line=line_number, text=line))
            if truncated:
                break
        return GrepResult(matches=matches, truncated=truncated)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        root = (path or "/").rstrip("/") + "/"
        values = [
            FileInfo(path=name, is_dir=False, size=len(content.encode()), modified_at="")
            for name, content in sorted(self._files.items())
            if name.startswith(root) and fnmatch.fnmatch(name, pattern)
        ]
        return GlobResult(matches=values)

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error="permission_denied")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error="permission_denied")

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error="permission_denied")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(
                path=path,
                content=self._files[path].encode() if path in self._files else None,
                error=None if path in self._files else "file_not_found",
            )
            for path in paths
        ]


class PlatformSkillMaterializer[TSession: Session]:
    """每次 graph 构造前以短事务复核冻结 ref 与业务内容。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        skill_repo_factory: Callable[[TSession], SkillRepository],
        platform_skills: tuple[SkillVersion, ...],
    ) -> None:
        self._session_factory = session_factory
        self._skill_repo_factory = skill_repo_factory
        self._platform = {(value.skill_id, value.version): value for value in platform_skills}

    async def materialize(self, request: RuntimeTurnRequest) -> SkillRuntimeMaterialization:
        refs = request.policy_snapshot.skill_refs
        if not refs:
            return SkillRuntimeMaterialization(ReadOnlySkillBackend({}), ())
        values: list[SkillVersion] = []
        async with self._session_factory() as session:
            repo = self._skill_repo_factory(session)
            for ref in refs:
                value = (
                    self._platform.get((ref.skill_id, ref.version))
                    if ref.source is SkillSource.PLATFORM
                    else await repo.get_owner_version(
                        ref.skill_id, ref.version, request.policy_snapshot.owner_id
                    )
                )
                if value is None or not _matches_ref(value, ref):
                    raise _error("runtime_skill_version_invalid", "Skill 冻结版本不可用")
                values.append(value)
        files: dict[str, str] = {}
        sources: list[str] = []
        for value in values:
            source = f"/skills/{value.source.value}/"
            path = f"/{value.source.value}/{value.name}/SKILL.md"
            if path in files:
                raise _error("runtime_skill_name_conflict", "Skill 名称冲突")
            files[path] = value.render_skill_md()
            if source not in sources:
                sources.append(source)
        return SkillRuntimeMaterialization(ReadOnlySkillBackend(files), tuple(sources))


def _matches_ref(value: SkillVersion, ref: Any) -> bool:
    return (
        value.skill_id == ref.skill_id
        and value.source is ref.source
        and value.version == ref.version
        and value.name == ref.name
        and value.content_hash == ref.content_hash
        and value.required_tool_names == ref.required_tool_names
    )


def _error(code: str, message: str) -> ResearchAgentRuntimeError:
    return ResearchAgentRuntimeError(
        kind=RuntimeErrorKind.PERMANENT, code=code, safe_message=message
    )
