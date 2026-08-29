"""本地开发脚本的静态输出契约。"""

from pathlib import Path


def test_worker_metrics_disabled_output_does_not_publish_port_zero_url() -> None:
    """port=0 分支必须明确显示禁用，不能打印无效 scrape URL。"""
    script = (Path(__file__).parents[2] / "scripts" / "dev.sh").read_text()

    assert 'if [[ "${worker_metrics_port}" == "0" ]]' in script
    assert "Worker Metrics: 已禁用（AGENT_WORKER_METRICS_PORT=0）" in script
    assert "http://127.0.0.1:%s/metrics" in script
    assert "http://127.0.0.1:0/metrics" not in script
    assert script.index("Worker Metrics: 已禁用") < script.index("http://127.0.0.1:%s/metrics")


def test_dev_script_keeps_research_agent_fake_by_default_and_worker_only_secret() -> None:
    """Fake 模式必须显式离线，真实 Agent Key 只能进入 Worker。"""
    script = (Path(__file__).parents[2] / "scripts" / "dev.sh").read_text()

    assert 'export AGENT_RESEARCH_RUNTIME_BACKEND="fake"' in script
    assert (
        "unset AGENT_EMBEDDING_API_KEY AGENT_CHAT_API_KEY "
        "AGENT_RESEARCH_MODEL_API_KEY" in script
    )


def test_dev_script_disables_duplicate_uvicorn_access_log() -> None:
    """项目中间件已有安全请求日志，不再重复输出 Uvicorn access log。"""
    script = (Path(__file__).parents[2] / "scripts" / "dev.sh").read_text()

    assert "--no-access-log" in script
