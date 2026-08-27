from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from typing import cast

import pytest
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from literature_agent.application.ports.research_agent_runtime import (
    ResearchAgentRuntimeError,
    RuntimeErrorKind,
    RuntimeTurnRequest,
)
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.skill_repository import SkillRepository
from literature_agent.domain.research_agent import (
    create_context_snapshot,
    create_policy_snapshot,
)
from literature_agent.domain.skill_configuration import (
    SkillCatalog,
    SkillProfileSelection,
    SkillSource,
    create_owner_skill,
    create_skill_profile,
    create_skill_version,
)
from literature_agent.infrastructure.agent.deep_agents_research_agent_runtime import (
    DeepAgentsResearchAgentRuntime,
)
from literature_agent.infrastructure.agent.skill_backend import (
    PlatformSkillMaterializer,
    ReadOnlySkillBackend,
)
from literature_agent.infrastructure.agent.skill_catalog import EVIDENCE_LED_SYNTHESIS
from tests.fakes.deep_agent_model import ScriptedDeepAgentChatModel


class _CountingSkillBackend(ReadOnlySkillBackend):
    def __init__(self, files: dict[str, str]) -> None:
        super().__init__(files)
        self.download_count = 0

    def download_files(self, paths: list[str]):
        self.download_count += 1
        return super().download_files(paths)


class _SkillReadingModel(ScriptedDeepAgentChatModel):
    def _next_message(self, messages: list[BaseMessage]) -> AIMessage:
        text = tuple(message.text for message in messages)
        self.observed_message_text.append(text)
        self.model_call_count += 1
        if self.model_call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {
                            "file_path": ("/skills/platform/evidence-led-synthesis/SKILL.md"),
                            "limit": 1000,
                        },
                        "id": "read-skill-1",
                        "type": "tool_call",
                    }
                ],
            )
        if self.model_call_count == 2 and any(
            isinstance(item, ToolMessage) for item in messages[-2:]
        ):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {
                            "command": ("test -e /skills/platform/evidence-led-synthesis/SKILL.md")
                        },
                        "id": "execute-skill-path-1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="当前授权上下文证据不足。")


class _SecondTurnModel(ScriptedDeepAgentChatModel):
    def _next_message(self, messages: list[BaseMessage]) -> AIMessage:
        self.observed_message_text.append(tuple(message.text for message in messages))
        self.model_call_count += 1
        return AIMessage(content="当前授权上下文证据不足。")


class _PhysicalSandbox(BaseSandbox):
    def __init__(self) -> None:
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "physical-sandbox"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        self.commands.append(command)
        return ExecuteResponse(output="missing", exit_code=1)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, error="file_not_found") for path in paths]


def _request(turn_run_id: str, ref, *, sequence: int) -> RuntimeTurnRequest:
    message_id = f"message-{turn_run_id}"
    context = create_context_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id=turn_run_id,
        user_message_id=message_id,
        history_through_sequence=sequence,
        review_output_id="review-output-1",
    )
    policy = create_policy_snapshot(
        owner_id="owner-1",
        project_id="project-1",
        session_id="session-1",
        turn_run_id=turn_run_id,
        allowed_tool_names=(
            "read_file",
            "execute",
            "read_review_evidence_matrix",
            "search_project_chunks",
        ),
        allowed_skill_names=(ref.name,),
        skill_refs=(ref,),
        max_model_calls=8,
        max_tool_calls=4,
    )
    return RuntimeTurnRequest(
        session_id="session-1",
        turn_run_id=turn_run_id,
        user_message_id=message_id,
        user_message_content=f"第 {sequence} 轮",
        context_snapshot=context,
        policy_snapshot=policy,
    )


async def test_native_skills_load_once_across_two_turns_and_remain_read_only() -> None:
    profile = create_skill_profile(
        owner_id="owner-1",
        session_id="session-1",
        selections=(
            SkillProfileSelection(SkillSource.PLATFORM, EVIDENCE_LED_SYNTHESIS.skill_id, 1),
        ),
    )
    ref = SkillCatalog(platform_skills=(EVIDENCE_LED_SYNTHESIS,)).resolve_profile(
        profile,
        owner_id="owner-1",
        allowed_tool_names=(
            "read_review_evidence_matrix",
            "search_project_chunks",
        ),
    )[0]
    backend = _CountingSkillBackend(
        {"/platform/evidence-led-synthesis/SKILL.md": (EVIDENCE_LED_SYNTHESIS.render_skill_md())}
    )
    first_model = _SkillReadingModel()
    second_model = _SecondTurnModel()
    sandbox = _PhysicalSandbox()
    checkpointer = MemorySaver()
    first_runtime = DeepAgentsResearchAgentRuntime(
        model=first_model,
        checkpointer=checkpointer,
        backend=sandbox,
        skill_backend=backend,
        skill_sources=("/skills/platform/",),
    )
    async for _ in first_runtime.execute_turn(_request("turn-1", ref, sequence=1)):
        pass
    second_runtime = DeepAgentsResearchAgentRuntime(
        model=second_model,
        checkpointer=checkpointer,
        backend=sandbox,
        skill_backend=backend,
        skill_sources=("/skills/platform/",),
    )
    async for _ in second_runtime.execute_turn(_request("turn-2", ref, sequence=2)):
        pass

    assert backend.download_count == 1
    assert sandbox.commands == ["test -e /skills/platform/evidence-led-synthesis/SKILL.md"]
    assert any(
        "evidence-led-synthesis" in part
        for model in (first_model, second_model)
        for call in model.observed_message_text
        for part in call
    )
    before = backend.download_files(["/platform/evidence-led-synthesis/SKILL.md"])[0].content
    assert backend.write("/platform/evidence-led-synthesis/SKILL.md", "tampered").error
    assert backend.edit("/platform/evidence-led-synthesis/SKILL.md", "Evidence", "tampered").error
    assert backend.upload_files([("/platform/evidence-led-synthesis/SKILL.md", b"tampered")])[
        0
    ].error
    assert backend.delete("/platform/evidence-led-synthesis").error
    after = backend.download_files(["/platform/evidence-led-synthesis/SKILL.md"])[0].content
    assert before == after


class _OwnerVersionRepository:
    def __init__(self, value) -> None:
        self.value = value
        self.lookups: list[tuple[str, int, str]] = []

    async def get_owner_version(self, skill_id: str, version: int, owner_id: str):
        self.lookups.append((skill_id, version, owner_id))
        return self.value


@asynccontextmanager
async def _fake_session_factory():
    yield object()


def _materializer(repo: _OwnerVersionRepository) -> PlatformSkillMaterializer[Session]:
    return PlatformSkillMaterializer(
        session_factory=cast(
            Callable[[], AbstractAsyncContextManager[Session]],
            _fake_session_factory,
        ),
        skill_repo_factory=cast(
            Callable[[Session], SkillRepository],
            lambda _: repo,
        ),
        platform_skills=(),
    )


def _owner_ref_and_version():
    skill = create_owner_skill(owner_id="owner-1", name="owner-synthesis")
    version = create_skill_version(
        skill=skill,
        description="按证据综合",
        instructions="只读取已授权证据。",
        required_tool_names=("search_project_chunks",),
    )
    profile = create_skill_profile(
        owner_id="owner-1",
        session_id="session-1",
        selections=(SkillProfileSelection(SkillSource.OWNER, skill.skill_id, 1),),
    )
    ref = SkillCatalog(platform_skills=(), owner_skills=(version,)).resolve_profile(
        profile,
        owner_id="owner-1",
        allowed_tool_names=("search_project_chunks",),
    )[0]
    return skill, version, ref


async def test_materializer_reads_owner_exact_version_with_policy_owner() -> None:
    _, version, ref = _owner_ref_and_version()
    repo = _OwnerVersionRepository(version)
    materializer = _materializer(repo)

    result = await materializer.materialize(_request("turn-owner", ref, sequence=1))

    assert repo.lookups == [(version.skill_id, version.version, "owner-1")]
    downloaded = result.backend.download_files(["/owner/owner-synthesis/SKILL.md"])[0]
    assert downloaded.content == version.render_skill_md().encode()
    assert result.sources == ("/skills/owner/",)


@pytest.mark.parametrize("drift", ["missing", "version", "hash", "name", "tools"])
async def test_materializer_rejects_missing_or_drifted_owner_version_without_body_leak(
    drift: str,
) -> None:
    skill, version, ref = _owner_ref_and_version()
    if drift == "missing":
        stored = None
    elif drift == "version":
        stored = create_skill_version(
            skill=skill,
            description=version.description,
            instructions=version.instructions,
            required_tool_names=version.required_tool_names,
            previous=version,
        )
    elif drift == "hash":
        stored = create_skill_version(
            skill=skill,
            description=version.description,
            instructions="这是不应进入错误消息的漂移正文。",
            required_tool_names=version.required_tool_names,
        )
    elif drift == "name":
        stored = create_skill_version(
            skill=replace(skill, name="renamed-synthesis"),
            description=version.description,
            instructions=version.instructions,
            required_tool_names=version.required_tool_names,
        )
    else:
        stored = create_skill_version(
            skill=skill,
            description=version.description,
            instructions=version.instructions,
            required_tool_names=(),
        )
    repo = _OwnerVersionRepository(stored)
    materializer = _materializer(repo)

    with pytest.raises(ResearchAgentRuntimeError) as caught:
        await materializer.materialize(_request("turn-drift", ref, sequence=1))

    assert caught.value.kind is RuntimeErrorKind.PERMANENT
    assert caught.value.code == "runtime_skill_version_invalid"
    assert caught.value.safe_message == "Skill 冻结版本不可用"
    assert "漂移正文" not in str(caught.value)
