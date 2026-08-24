"""浏览器 E2E 隔离启动脚本的离线模式契约。"""

from pathlib import Path


def test_e2e_harness_selects_every_fake_adapter_and_drops_provider_keys() -> None:
    """Playwright 不得继承真实 Provider 凭证或遗漏 Fake arXiv。"""
    script = (Path(__file__).parents[2] / "web" / "e2e" / "run.sh").read_text()

    assert 'export AGENT_PARSER_BACKEND="fake"' in script
    assert 'export AGENT_EMBEDDING_BACKEND="fake"' in script
    assert 'export AGENT_CHAT_BACKEND="fake"' in script
    assert 'export AGENT_ARXIV_BACKEND="fake"' in script
    assert "unset AGENT_EMBEDDING_API_KEY AGENT_CHAT_API_KEY" in script
    assert ".env" not in script
