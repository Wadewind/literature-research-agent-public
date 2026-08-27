from dataclasses import replace

import pytest

from literature_agent.domain.skill_configuration import (
    SkillCatalog,
    SkillProfileSelection,
    SkillSource,
    create_owner_skill,
    create_skill_profile,
    create_skill_version,
    update_skill_profile,
)


def test_owner_skill_versions_are_immutable_and_content_addressed() -> None:
    skill = create_owner_skill(owner_id="owner-1", name="systematic-search")
    first = create_skill_version(
        skill=skill,
        description="检索并筛选系统综述候选论文",
        instructions="# Workflow\n\n仅使用已授权检索工具。",
        required_tool_names=("arxiv-search_search_papers",),
    )
    replay = create_skill_version(
        skill=skill,
        description=first.description,
        instructions=first.instructions,
        required_tool_names=first.required_tool_names,
    )
    second = create_skill_version(
        skill=skill,
        description="检索并筛选系统综述候选论文",
        instructions="# Workflow\n\n先定义纳排标准，再检索。",
        required_tool_names=("arxiv-search_search_papers",),
        previous=first,
    )

    assert first.version == replay.version == 1
    assert first.content_hash == replay.content_hash
    assert second.version == 2
    assert second.content_hash != first.content_hash
    assert second.render_skill_md().startswith("---\nname: systematic-search\n")
    with pytest.raises(ValueError, match="content_hash"):
        replace(first, instructions="被篡改但沿用旧 hash")


def test_profile_resolution_is_stable_and_rejects_permission_expansion() -> None:
    skill = create_owner_skill(owner_id="owner-1", name="systematic-search")
    version = create_skill_version(
        skill=skill,
        description="检索论文",
        instructions="只使用授权来源。",
        required_tool_names=("arxiv-search_search_papers",),
    )
    catalog = SkillCatalog(platform_skills=(), owner_skills=(version,))
    profile = create_skill_profile(
        owner_id="owner-1",
        session_id="session-1",
        selections=(SkillProfileSelection(SkillSource.OWNER, skill.skill_id, 1),),
    )

    refs = catalog.resolve_profile(
        profile,
        owner_id="owner-1",
        allowed_tool_names=("arxiv-search_search_papers",),
    )
    assert refs[0].content_hash == version.content_hash
    assert refs[0].profile_revision == 1

    with pytest.raises(ValueError, match="权限"):
        catalog.resolve_profile(profile, owner_id="owner-1", allowed_tool_names=())

    updated = update_skill_profile(profile, selections=())
    assert updated.revision == 2
    assert refs[0].profile_revision == 1


def test_profile_hash_is_independent_of_selection_order() -> None:
    first = create_owner_skill(owner_id="owner-1", name="first-skill")
    second = create_owner_skill(owner_id="owner-1", name="second-skill")
    selections = (
        SkillProfileSelection(SkillSource.OWNER, first.skill_id, 1),
        SkillProfileSelection(SkillSource.OWNER, second.skill_id, 2),
    )

    forward = create_skill_profile(
        owner_id="owner-1", session_id="session-1", selections=selections
    )
    reversed_order = create_skill_profile(
        owner_id="owner-1",
        session_id="session-1",
        selections=tuple(reversed(selections)),
    )

    assert forward.config_hash == reversed_order.config_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "../escape"),
        ("name", "has--gap"),
        ("description", ""),
        ("instructions", ""),
        ("instructions", "x" * 32_001),
    ],
)
def test_owner_skill_rejects_untrusted_file_shape(field: str, value: str) -> None:
    if field == "name":
        with pytest.raises(ValueError):
            create_owner_skill(owner_id="owner-1", name=value)
        return
    skill = create_owner_skill(owner_id="owner-1", name="safe-skill")
    kwargs = {
        "description": "description",
        "instructions": "instructions",
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        create_skill_version(skill=skill, required_tool_names=(), **kwargs)
