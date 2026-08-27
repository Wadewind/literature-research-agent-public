"""AgentArtifact 不可信文件源的 SDK-neutral Port。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AgentArtifactSourceScope:
    owner_id: str
    project_id: str
    session_id: str
    turn_run_id: str
    sandbox_generation: int
    sandbox_fencing_token: int


class AgentArtifactSource(Protocol):
    """一次 fenced Runtime 调用提供的普通文件读取能力。"""

    @property
    def scope(self) -> AgentArtifactSourceScope: ...

    async def assert_current(self) -> None: ...

    async def read_regular_file(self, path: str, *, max_bytes: int) -> bytes: ...
