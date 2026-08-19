"""健康检查的应用服务。"""

from literature_agent.domain.health import HealthStatus


class HealthService:
    """服务健康的用例层。

    当前仅独立报告进程存活状态。等后续引入数据库、队列等适配器后，
    再补充就绪检查。
    """

    def get_live_status(self) -> HealthStatus:
        """返回进程的存活状态。"""
        return HealthStatus(status="ok")
