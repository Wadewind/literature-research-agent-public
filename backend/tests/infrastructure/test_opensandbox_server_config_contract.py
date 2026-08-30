"""项目维护的 OpenSandbox Server 配置静态契约。"""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[3]
CONFIG_PATH = PROJECT_ROOT / "config" / "opensandbox-server.phase6.toml"


def test_project_server_config_is_loopback_bridge_and_default_deny_ready() -> None:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["server"] == {
        "host": "127.0.0.1",
        "port": 8080,
        "max_sandbox_timeout_seconds": 3600,
        "limit_concurrency": 64,
    }
    assert config["runtime"] == {
        "type": "docker",
        "execd_image": (
            "opensandbox/execd@sha256:"
            "1dc98c7de10b9a73450ac75aa0f200ad7972f2c40f5225f6a8998e166b45d6dd"
        ),
    }
    assert config["storage"]["allowed_host_paths"] == []
    assert config["docker"]["network_mode"] == "bridge"
    assert config["docker"]["drop_capabilities"] == ["ALL"]
    assert config["docker"]["no_new_privileges"] is True
    assert config["docker"]["pids_limit"] == 256
    assert config["docker"]["port_range_max"] - config["docker"]["port_range_min"] == 100
    assert config["egress"] == {
        "image": (
            "opensandbox/egress@sha256:"
            "973130e01bf76e8e686e2853ebf47b21741bc8781919bb4a7cf60af09a3c6e8a"
        ),
        "mode": "dns+nft",
        "disable_ipv6": True,
    }
    assert config["renew_intent"]["enabled"] is False


def test_server_start_script_requires_pinned_server_and_api_key() -> None:
    script = (PROJECT_ROOT / "scripts" / "opensandbox-server.sh").read_text(
        encoding="utf-8"
    )

    assert 'EXPECTED_SERVER_VERSION="0.2.2"' in script
    assert "OPENSANDBOX_SERVER_API_KEY" in script
    assert "OPENSANDBOX_INSECURE_SERVER" not in script
    assert 'exec "${server_executable}" --config "${CONFIG_FILE}"' in script


def test_real_deep_agents_dev_script_manages_local_server_lifecycle() -> None:
    script = (PROJECT_ROOT / "scripts" / "dev.sh").read_text(encoding="utf-8")

    assert '"${AGENT_RESEARCH_RUNTIME_BACKEND:-fake}" == "deep_agents"' in script
    assert "deep_agents 模式缺少配置: AGENT_RESEARCH_SANDBOX_API_KEY" in script
    assert 'OPENSANDBOX_SERVER_API_KEY="${AGENT_RESEARCH_SANDBOX_API_KEY}"' in script
    assert "http://127.0.0.1:8080/health" in script
    assert 'child_pids+=("${opensandbox_pid}")' in script
    assert "unset AGENT_RESEARCH_SANDBOX_API_KEY" in script
    assert "OPENSANDBOX_INSECURE_SERVER" not in script
