"""Research Agent 固定 Sandbox 镜像 recipe 契约。"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[3]


def test_vnc_wrapper_keeps_password_out_of_frontend_and_listens_only_on_loopback() -> None:
    dockerfile = (REPOSITORY_ROOT / "sandbox/research-agent/Dockerfile").read_text(encoding="utf-8")
    wrapper = (REPOSITORY_ROOT / "sandbox/research-agent/start-vnc-server").read_text(
        encoding="utf-8"
    )
    overlay = (REPOSITORY_ROOT / "sandbox/research-agent/Dockerfile.vnc-overlay").read_text(
        encoding="utf-8"
    )

    assert "COPY --chmod=0755 start-vnc-server" in dockerfile
    assert "/opt/research-agent-venv/bin/Xtigervnc" in dockerfile
    assert 'exec /usr/bin/Xtigervnc "$@" -SecurityTypes None -localhost' in wrapper
    assert "Password" not in wrapper
    assert "FROM agent-service/research-agent-sandbox@sha256:" in overlay
    assert "COPY --chmod=0755 start-vnc-server" in overlay
