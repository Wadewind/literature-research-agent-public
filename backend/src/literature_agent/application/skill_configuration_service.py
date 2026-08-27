"""平台/owner Skill 与首个 Turn 前 Session Profile 配置用例。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TypeVar

from literature_agent.application.ports.agent_repository import AgentRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.ports.skill_repository import SkillRepository
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import (
    AgentSessionNotFoundError,
    SkillConfigurationInvalidError,
    SkillNotFoundError,
    SkillProfileLockedError,
    SkillProfileRevisionConflictError,
    SkillVersionConflictError,
)
from literature_agent.domain.skill_configuration import (
    SkillProfile,
    SkillProfileSelection,
    SkillSource,
    SkillVersion,
    create_owner_skill,
    create_skill_profile,
    create_skill_version,
    empty_skill_profile_hash,
    update_skill_profile,
)

TSession = TypeVar("TSession", bound=Session)


@dataclass(frozen=True, slots=True)
class SkillProfileView:
    session_id: str
    revision: int
    selections: tuple[SkillProfileSelection, ...]
    config_hash: str


class SkillConfigurationService[TSession: Session]:
    """只接受声明式内容和稳定 Skill 选择，不接受路径或可执行附件。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        agent_repo_factory: Callable[[TSession], AgentRepository],
        skill_repo_factory: Callable[[TSession], SkillRepository],
        platform_skills: tuple[SkillVersion, ...],
    ) -> None:
        self._session_factory = session_factory
        self._agent_repo_factory = agent_repo_factory
        self._skill_repo_factory = skill_repo_factory
        self._platform_skills = platform_skills

    async def list_available(self, actor: ActorContext) -> tuple[SkillVersion, ...]:
        async with self._session_factory() as session:
            owner = await self._skill_repo_factory(session).list_owner_skills(actor.owner_id)
            return (*self._platform_skills, *owner)

    async def create_owner_skill(
        self,
        actor: ActorContext,
        *,
        name: str,
        description: str,
        instructions: str,
        required_tool_names: tuple[str, ...],
    ) -> SkillVersion:
        try:
            identity = create_owner_skill(owner_id=actor.owner_id, name=name)
            version = create_skill_version(
                skill=identity,
                description=description,
                instructions=instructions,
                required_tool_names=required_tool_names,
            )
        except ValueError as exc:
            raise SkillConfigurationInvalidError(str(exc)) from exc
        async with self._session_factory() as session:
            repo = self._skill_repo_factory(session)
            if await repo.get_owner_skill_by_name(actor.owner_id, identity.name) is not None:
                raise SkillConfigurationInvalidError("owner Skill name 已存在")
            if not await repo.add_owner_skill(identity):
                raise SkillConfigurationInvalidError("owner Skill name 已存在")
            await session.flush()
            await repo.add_version(version)
            await session.commit()
            return version

    async def create_owner_version(
        self,
        actor: ActorContext,
        skill_id: str,
        *,
        expected_version: int,
        description: str,
        instructions: str,
        required_tool_names: tuple[str, ...],
    ) -> SkillVersion:
        async with self._session_factory() as session:
            repo = self._skill_repo_factory(session)
            # 锁稳定身份而非“当前最新版本”行，避免并发插入新版本绕过 CAS。
            identity = await repo.get_owner_skill(
                skill_id, actor.owner_id, for_update=True
            )
            latest = await repo.get_latest_owner_version(skill_id, actor.owner_id)
            if identity is None or latest is None:
                raise SkillNotFoundError(skill_id)
            if latest.version != expected_version:
                raise SkillVersionConflictError(skill_id)
            try:
                value = create_skill_version(
                    skill=identity,
                    description=description,
                    instructions=instructions,
                    required_tool_names=required_tool_names,
                    previous=latest,
                )
            except ValueError as exc:
                raise SkillConfigurationInvalidError(str(exc)) from exc
            if value.content_hash == latest.content_hash:
                return latest
            await repo.add_version(value)
            await session.commit()
            return value

    async def get_profile(self, actor: ActorContext, session_id: str) -> SkillProfileView:
        async with self._session_factory() as session:
            if (
                await self._agent_repo_factory(session).get_session_scoped(
                    session_id, actor.owner_id
                )
                is None
            ):
                raise AgentSessionNotFoundError(session_id)
            profile = await self._skill_repo_factory(session).get_profile(
                session_id, actor.owner_id
            )
            return _profile_view(profile, session_id)

    async def update_profile(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        expected_revision: int,
        selections: tuple[SkillProfileSelection, ...],
    ) -> SkillProfileView:
        if expected_revision < 0:
            raise SkillProfileRevisionConflictError(session_id)
        async with self._session_factory() as session:
            agent_repo = self._agent_repo_factory(session)
            if await agent_repo.get_session_scoped_for_update(session_id, actor.owner_id) is None:
                raise AgentSessionNotFoundError(session_id)
            if await agent_repo.has_messages(session_id):
                raise SkillProfileLockedError(session_id)
            repo = self._skill_repo_factory(session)
            await self._validate_selections(repo, actor.owner_id, selections)
            current = await repo.get_profile_for_update(session_id, actor.owner_id)
            try:
                if current is None:
                    if expected_revision != 0:
                        raise SkillProfileRevisionConflictError(session_id)
                    value = create_skill_profile(
                        owner_id=actor.owner_id,
                        session_id=session_id,
                        selections=selections,
                    )
                    await repo.add_profile(value)
                else:
                    if current.revision != expected_revision:
                        raise SkillProfileRevisionConflictError(session_id)
                    value = update_skill_profile(current, selections=selections)
                    if not await repo.save_profile(value, expected_revision=expected_revision):
                        raise SkillProfileRevisionConflictError(session_id)
            except ValueError as exc:
                raise SkillConfigurationInvalidError(str(exc)) from exc
            await session.commit()
            return _profile_view(value, session_id)

    async def _validate_selections(
        self,
        repo: SkillRepository,
        owner_id: str,
        selections: tuple[SkillProfileSelection, ...],
    ) -> None:
        platform = {(item.skill_id, item.version): item for item in self._platform_skills}
        names: set[str] = set()
        for selection in selections:
            value = (
                platform.get((selection.skill_id, selection.version))
                if selection.source is SkillSource.PLATFORM
                else await repo.get_owner_version(selection.skill_id, selection.version, owner_id)
            )
            if value is None or value.source is not selection.source:
                raise SkillConfigurationInvalidError("Skill 版本不可用")
            if value.name in names:
                raise SkillConfigurationInvalidError("Skill Profile 名称不能重复")
            names.add(value.name)


def _profile_view(value: SkillProfile | None, session_id: str) -> SkillProfileView:
    if value is None:
        return SkillProfileView(session_id, 0, (), empty_skill_profile_hash())
    return SkillProfileView(value.session_id, value.revision, value.selections, value.config_hash)
