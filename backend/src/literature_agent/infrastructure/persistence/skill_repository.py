"""Owner Skill 与 Session Skill Profile 的 PostgreSQL Repository。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.skill_repository import SkillRepository
from literature_agent.domain.skill_configuration import (
    OwnerSkill,
    SkillProfile,
    SkillProfileSelection,
    SkillSource,
    SkillVersion,
)
from literature_agent.infrastructure.persistence.models import (
    AgentOwnerSkillORM,
    AgentOwnerSkillVersionORM,
    AgentSessionORM,
    AgentSkillProfileORM,
)


class SqlalchemySkillRepository(SkillRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_owner_skills(self, owner_id: str) -> list[SkillVersion]:
        rows = (
            await self._session.execute(
                select(AgentOwnerSkillVersionORM, AgentOwnerSkillORM)
                .join(
                    AgentOwnerSkillORM,
                    AgentOwnerSkillVersionORM.skill_id == AgentOwnerSkillORM.skill_id,
                )
                .where(
                    AgentOwnerSkillORM.owner_id == owner_id,
                    AgentOwnerSkillVersionORM.owner_id == owner_id,
                )
                .order_by(AgentOwnerSkillORM.name, AgentOwnerSkillVersionORM.version)
            )
        ).all()
        return [_version(version, skill) for version, skill in rows]

    async def get_owner_skill(
        self, skill_id: str, owner_id: str, *, for_update: bool = False
    ) -> OwnerSkill | None:
        statement = select(AgentOwnerSkillORM).where(
            AgentOwnerSkillORM.skill_id == skill_id,
            AgentOwnerSkillORM.owner_id == owner_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return _skill(row) if row else None

    async def get_owner_skill_by_name(self, owner_id: str, name: str) -> OwnerSkill | None:
        row = (
            await self._session.execute(
                select(AgentOwnerSkillORM).where(
                    AgentOwnerSkillORM.owner_id == owner_id,
                    AgentOwnerSkillORM.name == name,
                )
            )
        ).scalar_one_or_none()
        return _skill(row) if row else None

    async def get_owner_version(
        self, skill_id: str, version: int, owner_id: str
    ) -> SkillVersion | None:
        row = (
            await self._session.execute(
                select(AgentOwnerSkillVersionORM, AgentOwnerSkillORM)
                .join(
                    AgentOwnerSkillORM,
                    AgentOwnerSkillVersionORM.skill_id == AgentOwnerSkillORM.skill_id,
                )
                .where(
                    AgentOwnerSkillVersionORM.skill_id == skill_id,
                    AgentOwnerSkillVersionORM.version == version,
                    AgentOwnerSkillVersionORM.owner_id == owner_id,
                    AgentOwnerSkillORM.owner_id == owner_id,
                )
            )
        ).one_or_none()
        return _version(*row) if row else None

    async def get_latest_owner_version(
        self, skill_id: str, owner_id: str, *, for_update: bool = False
    ) -> SkillVersion | None:
        statement = (
            select(AgentOwnerSkillVersionORM, AgentOwnerSkillORM)
            .join(
                AgentOwnerSkillORM,
                AgentOwnerSkillVersionORM.skill_id == AgentOwnerSkillORM.skill_id,
            )
            .where(
                AgentOwnerSkillVersionORM.skill_id == skill_id,
                AgentOwnerSkillVersionORM.owner_id == owner_id,
                AgentOwnerSkillORM.owner_id == owner_id,
            )
            .order_by(AgentOwnerSkillVersionORM.version.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).one_or_none()
        return _version(*row) if row else None

    async def add_owner_skill(self, value: OwnerSkill) -> bool:
        statement = (
            pg_insert(AgentOwnerSkillORM)
            .values(
                skill_id=value.skill_id,
                owner_id=value.owner_id,
                name=value.name,
                created_at=value.created_at,
            )
            .on_conflict_do_nothing(index_elements=["owner_id", "name"])
            .returning(AgentOwnerSkillORM.skill_id)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def add_version(self, value: SkillVersion) -> SkillVersion:
        if value.source is not SkillSource.OWNER or value.owner_id is None:
            raise ValueError("只持久化 owner Skill")
        self._session.add(
            AgentOwnerSkillVersionORM(
                skill_id=value.skill_id,
                version=value.version,
                owner_id=value.owner_id,
                description=value.description,
                instructions=value.instructions,
                required_tool_names=list(value.required_tool_names),
                content_hash=value.content_hash,
                created_at=value.created_at,
            )
        )
        return value

    async def get_profile(self, session_id: str, owner_id: str) -> SkillProfile | None:
        return await self._get_profile(session_id, owner_id, for_update=False)

    async def get_profile_for_update(self, session_id: str, owner_id: str) -> SkillProfile | None:
        return await self._get_profile(session_id, owner_id, for_update=True)

    async def _get_profile(
        self, session_id: str, owner_id: str, *, for_update: bool
    ) -> SkillProfile | None:
        statement = (
            select(AgentSkillProfileORM)
            .join(AgentSessionORM, AgentSkillProfileORM.session_id == AgentSessionORM.session_id)
            .where(
                AgentSkillProfileORM.session_id == session_id,
                AgentSkillProfileORM.owner_id == owner_id,
                AgentSessionORM.owner_id == owner_id,
            )
            .order_by(AgentSkillProfileORM.revision.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return _profile(row) if row else None

    async def add_profile(self, value: SkillProfile) -> SkillProfile:
        self._session.add(
            AgentSkillProfileORM(
                profile_id=value.profile_id,
                session_id=value.session_id,
                owner_id=value.owner_id,
                revision=value.revision,
                selections=[
                    {
                        "source": item.source.value,
                        "skill_id": item.skill_id,
                        "version": item.version,
                    }
                    for item in value.selections
                ],
                config_hash=value.config_hash,
                created_at=value.created_at,
                updated_at=value.updated_at,
            )
        )
        return value

    async def save_profile(self, value: SkillProfile, *, expected_revision: int) -> bool:
        current = await self.get_profile_for_update(value.session_id, value.owner_id)
        if (
            current is None
            or current.profile_id != value.profile_id
            or current.revision != expected_revision
            or value.revision != expected_revision + 1
        ):
            return False
        await self.add_profile(value)
        return True


def _skill(row: AgentOwnerSkillORM) -> OwnerSkill:
    return OwnerSkill(row.skill_id, row.owner_id, row.name, row.created_at)


def _version(row: AgentOwnerSkillVersionORM, skill: AgentOwnerSkillORM) -> SkillVersion:
    return SkillVersion(
        skill_id=row.skill_id,
        source=SkillSource.OWNER,
        owner_id=row.owner_id,
        version=row.version,
        name=skill.name,
        description=row.description,
        instructions=row.instructions,
        required_tool_names=tuple(row.required_tool_names),
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


def _profile(row: AgentSkillProfileORM) -> SkillProfile:
    return SkillProfile(
        profile_id=row.profile_id,
        owner_id=row.owner_id,
        session_id=row.session_id,
        revision=row.revision,
        selections=tuple(
            SkillProfileSelection(
                source=SkillSource(item["source"]),
                skill_id=item["skill_id"],
                version=item["version"],
            )
            for item in row.selections
        ),
        config_hash=row.config_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
