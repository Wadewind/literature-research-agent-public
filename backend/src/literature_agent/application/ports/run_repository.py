"""Run Repository 端口。"""

from typing import Protocol

from literature_agent.domain.run import Run, RunStatus


class RunRepository(Protocol):
    """Run 持久化的抽象端口。"""

    async def add(self, run: Run) -> Run:
        """保存 Run。"""
        ...

    async def get_by_id(self, run_id: str) -> Run | None:
        """按 ID 查询 Run；不校验所有权。"""
        ...

    async def get_by_id_for_update(
        self,
        run_id: str,
        owner_id: str,
    ) -> Run | None:
        """在事务内锁定并查询 Run，同时校验所有权。

        返回 None 表示 Run 不存在或不属于指定 owner。
        """
        ...

    async def update_status(
        self,
        run_id: str,
        expected_status: RunStatus,
        new_status: RunStatus,
        new_event_sequence: int,
    ) -> bool:
        """条件更新 Run 状态与下一个 Event sequence。

        仅当当前状态等于 ``expected_status`` 时才更新。返回是否成功。
        """
        ...

    async def has_active_runs(self, project_id: str) -> bool:
        """判断 Project 是否存在非终态 Run。

        非终态状态见 ``domain.run.ACTIVE_RUN_STATUSES``。
        """
        ...

    async def get_latest_indexing_run_id(self, parse_revision_id: str) -> str | None:
        """查询指定 Parse Revision 最近一次 indexing Run 的 ID；不存在返回 None。

        按 ``input_payload.parse_revision_id`` 匹配，按创建时间倒序取第一条。
        """
        ...
