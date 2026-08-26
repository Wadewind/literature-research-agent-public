"""在业务成功事务内发布 WorkspaceSnapshot 的 SDK-neutral Port。"""

from typing import Protocol


class WorkspaceSnapshotPublisher(Protocol):
    """把当前 Turn 的 STAGED 快照条件推进为 STABLE。"""

    async def publish_for_success(
        self,
        *,
        owner_id: str,
        project_id: str,
        session_id: str,
        turn_run_id: str,
        required: bool,
    ) -> bool:
        """无快照且 required=false 时 no-op；其余失败返回 false。"""
        ...
