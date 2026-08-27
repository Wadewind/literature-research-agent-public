"""Research Skill Catalog、不可变版本、Session Profile 与逐 Turn 引用。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_DESCRIPTION = 1_024
_MAX_INSTRUCTIONS = 32_000
_MAX_REQUIRED_TOOLS = 32
_MAX_PROFILE_SKILLS = 8


class SkillSource(StrEnum):
    """Skill 的平台信任来源。"""

    PLATFORM = "platform"
    OWNER = "owner"


@dataclass(frozen=True, slots=True)
class OwnerSkill:
    """owner 范围的稳定 Skill 身份；版本内容另行追加。"""

    skill_id: str
    owner_id: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SkillVersion:
    """可由旧 PolicySnapshot 精确重建的不可变声明式版本。"""

    skill_id: str
    source: SkillSource
    owner_id: str | None
    version: int
    name: str
    description: str
    instructions: str
    required_tool_names: tuple[str, ...]
    content_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_name(self.name)
        if self.source is SkillSource.OWNER and not self.owner_id:
            raise ValueError("owner Skill 必须绑定 owner")
        if self.source is SkillSource.PLATFORM and self.owner_id is not None:
            raise ValueError("平台 Skill 不能绑定 owner")
        if self.version < 1:
            raise ValueError("Skill version 必须为正整数")
        _validate_content(self.description, self.instructions, self.required_tool_names)
        if not _SHA256.fullmatch(self.content_hash):
            raise ValueError("Skill content_hash 必须是小写 SHA-256")
        if (
            _content_hash(
                self.name,
                self.description,
                self.instructions,
                self.required_tool_names,
            )
            != self.content_hash
        ):
            raise ValueError("Skill 内容与 content_hash 不一致")

    def render_skill_md(self) -> str:
        """生成唯一受控 frontmatter；调用方不能提交 path/frontmatter。"""
        tools = ", ".join(json.dumps(name) for name in self.required_tool_names)
        return (
            "---\n"
            f"name: {self.name}\n"
            f"description: {json.dumps(self.description, ensure_ascii=False)}\n"
            f"allowed-tools: [{tools}]\n"
            "---\n\n"
            f"{self.instructions.rstrip()}\n"
        )


@dataclass(frozen=True, slots=True)
class SkillProfileSelection:
    source: SkillSource
    skill_id: str
    version: int

    def __post_init__(self) -> None:
        if not self.skill_id.strip() or len(self.skill_id) > 64 or self.version < 1:
            raise ValueError("Skill Profile selection 非法")


@dataclass(frozen=True, slots=True)
class SkillProfile:
    profile_id: str
    owner_id: str
    session_id: str
    revision: int
    selections: tuple[SkillProfileSelection, ...]
    config_hash: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SkillPolicyRef:
    """PolicySnapshot 中 SDK-neutral 的精确 Skill 内容引用。"""

    profile_id: str
    profile_revision: int
    skill_id: str
    source: SkillSource
    version: int
    name: str
    content_hash: str
    required_tool_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.profile_id or self.profile_revision < 1:
            raise ValueError("Skill Policy Profile revision 非法")
        if not self.skill_id or self.version < 1:
            raise ValueError("Skill Policy version 非法")
        _validate_name(self.name)
        _validate_tools(self.required_tool_names)
        if not _SHA256.fullmatch(self.content_hash):
            raise ValueError("Skill Policy content_hash 非法")


class SkillCatalog:
    """一次解析所需的平台和 owner 精确版本集合。"""

    def __init__(
        self,
        *,
        platform_skills: tuple[SkillVersion, ...],
        owner_skills: tuple[SkillVersion, ...] = (),
    ) -> None:
        versions = (*platform_skills, *owner_skills)
        keys = [(item.source, item.skill_id, item.version) for item in versions]
        if len(keys) != len(set(keys)):
            raise ValueError("Skill Catalog 版本不得重复")
        self._versions = dict(zip(keys, versions, strict=True))

    @property
    def platform_skills(self) -> tuple[SkillVersion, ...]:
        return tuple(
            item for item in self._versions.values() if item.source is SkillSource.PLATFORM
        )

    def resolve_profile(
        self,
        profile: SkillProfile | None,
        *,
        owner_id: str,
        allowed_tool_names: tuple[str, ...],
    ) -> tuple[SkillPolicyRef, ...]:
        if profile is None:
            return ()
        if profile.owner_id != owner_id:
            raise ValueError("Skill Profile owner 不匹配")
        refs: list[SkillPolicyRef] = []
        names: set[str] = set()
        allowed = set(allowed_tool_names)
        for selection in sorted(
            profile.selections, key=lambda item: (item.source.value, item.skill_id, item.version)
        ):
            version = self._versions.get((selection.source, selection.skill_id, selection.version))
            if version is None:
                raise ValueError("Skill 版本不可用")
            if version.source is SkillSource.OWNER and version.owner_id != owner_id:
                raise ValueError("owner Skill 不可见")
            if version.name in names:
                raise ValueError("Skill Profile 名称不能重复")
            if not set(version.required_tool_names).issubset(allowed):
                raise ValueError("Skill 所需 Tool 超出本轮权限")
            names.add(version.name)
            refs.append(
                SkillPolicyRef(
                    profile_id=profile.profile_id,
                    profile_revision=profile.revision,
                    skill_id=version.skill_id,
                    source=version.source,
                    version=version.version,
                    name=version.name,
                    content_hash=version.content_hash,
                    required_tool_names=version.required_tool_names,
                )
            )
        return tuple(refs)


def create_owner_skill(*, owner_id: str, name: str) -> OwnerSkill:
    if not owner_id.strip():
        raise ValueError("owner_id 不能为空")
    _validate_name(name)
    return OwnerSkill(str(uuid4()), owner_id, name, datetime.now(UTC))


def create_skill_version(
    *,
    skill: OwnerSkill,
    description: str,
    instructions: str,
    required_tool_names: tuple[str, ...],
    previous: SkillVersion | None = None,
) -> SkillVersion:
    _validate_content(description, instructions, required_tool_names)
    if previous is not None and (
        previous.skill_id != skill.skill_id
        or previous.source is not SkillSource.OWNER
        or previous.owner_id != skill.owner_id
        or previous.name != skill.name
    ):
        raise ValueError("Skill 旧版本身份不匹配")
    version = 1 if previous is None else previous.version + 1
    return SkillVersion(
        skill_id=skill.skill_id,
        source=SkillSource.OWNER,
        owner_id=skill.owner_id,
        version=version,
        name=skill.name,
        description=description.strip(),
        instructions=instructions.strip(),
        required_tool_names=tuple(required_tool_names),
        content_hash=_content_hash(
            skill.name,
            description.strip(),
            instructions.strip(),
            required_tool_names,
        ),
        created_at=datetime.now(UTC),
    )


def create_platform_skill(
    *,
    skill_id: str,
    version: int,
    name: str,
    description: str,
    instructions: str,
    required_tool_names: tuple[str, ...],
) -> SkillVersion:
    if not skill_id.strip() or len(skill_id) > 64:
        raise ValueError("平台 Skill ID 非法")
    return SkillVersion(
        skill_id=skill_id,
        source=SkillSource.PLATFORM,
        owner_id=None,
        version=version,
        name=name,
        description=description.strip(),
        instructions=instructions.strip(),
        required_tool_names=required_tool_names,
        content_hash=_content_hash(
            name, description.strip(), instructions.strip(), required_tool_names
        ),
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )


def create_skill_profile(
    *, owner_id: str, session_id: str, selections: tuple[SkillProfileSelection, ...]
) -> SkillProfile:
    _validate_profile(owner_id, session_id, selections)
    now = datetime.now(UTC)
    return SkillProfile(
        str(uuid4()),
        owner_id,
        session_id,
        1,
        tuple(selections),
        _profile_hash(selections),
        now,
        now,
    )


def update_skill_profile(
    profile: SkillProfile, *, selections: tuple[SkillProfileSelection, ...]
) -> SkillProfile:
    _validate_profile(profile.owner_id, profile.session_id, selections)
    return replace(
        profile,
        revision=profile.revision + 1,
        selections=tuple(selections),
        config_hash=_profile_hash(selections),
        updated_at=datetime.now(UTC),
    )


def empty_skill_profile_hash() -> str:
    return _profile_hash(())


def _validate_name(name: str) -> None:
    if not _SKILL_NAME.fullmatch(name) or "--" in name:
        raise ValueError("Skill name 必须是 1..64 位小写字母、数字和单连字符")


def _validate_content(
    description: str, instructions: str, required_tool_names: tuple[str, ...]
) -> None:
    if not description.strip() or len(description) > _MAX_DESCRIPTION:
        raise ValueError("Skill description 必须在 1..1024 字符")
    if not instructions.strip() or len(instructions) > _MAX_INSTRUCTIONS:
        raise ValueError("Skill instructions 必须在 1..32000 字符")
    if "\x00" in description or "\x00" in instructions:
        raise ValueError("Skill 文本不能包含 NUL")
    _validate_tools(required_tool_names)


def _validate_tools(names: tuple[str, ...]) -> None:
    if len(names) > _MAX_REQUIRED_TOOLS:
        raise ValueError("Skill 所需 Tool 数量超过上限")
    if len(names) != len(set(names)) or any(not _TOOL_NAME.fullmatch(x) for x in names):
        raise ValueError("Skill 所需 Tool 名称非法或重复")


def _validate_profile(
    owner_id: str, session_id: str, selections: tuple[SkillProfileSelection, ...]
) -> None:
    if not owner_id.strip() or not session_id.strip():
        raise ValueError("Skill Profile owner/session 不能为空")
    if len(selections) > _MAX_PROFILE_SKILLS:
        raise ValueError("Skill Profile 选择超过上限")
    keys = [(item.source, item.skill_id, item.version) for item in selections]
    if len(keys) != len(set(keys)):
        raise ValueError("Skill Profile 选择不能重复")


def _content_hash(
    name: str,
    description: str,
    instructions: str,
    required_tool_names: tuple[str, ...],
) -> str:
    tools = ", ".join(json.dumps(item) for item in required_tool_names)
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        f"allowed-tools: [{tools}]\n"
        "---\n\n"
        f"{instructions.rstrip()}\n"
    )
    return hashlib.sha256(content.encode()).hexdigest()


def _profile_hash(selections: tuple[SkillProfileSelection, ...]) -> str:
    payload = [
        {"source": item.source.value, "skill_id": item.skill_id, "version": item.version}
        for item in sorted(
            selections,
            key=lambda item: (item.source.value, item.skill_id, item.version),
        )
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
