#!/usr/bin/env bash
set -euo pipefail

# 升级 deepagents 前必须同时通过公开装配面与项目真实 Adapter 行为门禁。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"

cd "$BACKEND_DIR"
if [[ -x .venv/bin/pytest ]]; then
  PYTEST=(.venv/bin/pytest)
else
  PYTEST=(uv run pytest)
fi

"${PYTEST[@]}" -q \
  tests/application/test_research_agent_runtime_contract.py \
  tests/application/test_agent_turn_executor.py \
  tests/application/test_agent_usage_service.py \
  tests/infrastructure/test_deep_agents_upgrade_contract.py \
  tests/infrastructure/test_fake_research_agent_runtime.py \
  tests/infrastructure/test_deep_agents_research_agent_runtime.py \
  tests/infrastructure/test_sandboxed_research_agent_runtime.py \
  tests/infrastructure/test_mcp_tools.py \
  tests/infrastructure/test_native_skills.py \
  tests/integration/test_deep_agents_runtime_checkpoint.py \
  tests/integration/test_agent_runtime_process_recovery.py
