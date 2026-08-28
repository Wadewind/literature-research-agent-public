#!/usr/bin/env bash
set -euo pipefail

# Phase 6 的固定离线回归清单：不启用真实模型、实时公网或 OpenSandbox Smoke。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"

cd "$BACKEND_DIR"
if [[ -x .venv/bin/pytest ]]; then
  PYTEST=(.venv/bin/pytest)
else
  PYTEST=(uv run pytest)
fi

"${PYTEST[@]}" -q \
  tests/domain/test_agent_attachment.py \
  tests/domain/test_agent_network.py \
  tests/domain/test_agent_usage.py \
  tests/domain/test_browser_control.py \
  tests/domain/test_skill_configuration.py \
  tests/domain/test_workspace_snapshot.py \
  tests/application/test_agent_attachment_materializer.py \
  tests/application/test_agent_attachment_service.py \
  tests/application/test_agent_turn_executor.py \
  tests/application/test_agent_usage_service.py \
  tests/application/test_agent_artifact_service.py \
  tests/application/test_agent_artifact_publisher.py \
  tests/application/test_browser_control_service.py \
  tests/application/test_skill_configuration_service.py \
  tests/infrastructure/test_fake_research_agent_runtime.py \
  tests/infrastructure/test_deep_agents_upgrade_contract.py \
  tests/infrastructure/test_deep_agents_research_agent_runtime.py \
  tests/infrastructure/test_sandboxed_research_agent_runtime.py \
  tests/infrastructure/test_mcp_tools.py \
  tests/infrastructure/test_native_skills.py \
  tests/infrastructure/test_opensandbox_backend.py \
  tests/infrastructure/test_opensandbox_browser_control_smoke.py \
  tests/infrastructure/test_agent_artifact_tools.py \
  tests/infrastructure/test_agent_public_egress_schema_contract.py \
  tests/infrastructure/test_browser_gateway.py \
  tests/infrastructure/test_browser_proxy_recipe.py \
  tests/infrastructure/test_research_agent_sandbox_recipe.py \
  tests/infrastructure/test_sandbox_cleanup_service.py \
  tests/infrastructure/test_sandbox_workspace_manager.py \
  tests/api/test_agent_attachments.py \
  tests/api/test_agent_artifact_manifest.py \
  tests/api/test_agent_browser.py \
  tests/evaluation/test_agent_metrics.py \
  tests/evaluation/test_agent_scenarios.py
