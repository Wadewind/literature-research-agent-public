"""Worker 入口配置测试。"""

from literature_agent.infrastructure.config import Settings
from literature_agent.worker import execute_run, make_worker_settings


def test_make_worker_settings_registers_execute_run() -> None:
    """WorkerSettings 应注册 execute_run 并禁用 ARQ 自动重试。"""
    settings = Settings(redis_url="redis://example.internal:6380/2")

    worker_settings = make_worker_settings(settings)

    assert execute_run in worker_settings.functions
    assert worker_settings.max_tries == 1
    assert worker_settings.on_startup is not None
    assert worker_settings.on_shutdown is not None
    assert worker_settings.redis_settings.host == "example.internal"
    assert worker_settings.redis_settings.port == 6380
    assert worker_settings.redis_settings.database == 2
