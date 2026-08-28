#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${PROJECT_ROOT}/config/opensandbox-server.phase6.toml"
EXPECTED_SERVER_VERSION="0.2.2"

server_executable="$(command -v opensandbox-server || true)"
if [[ -z "${server_executable}" ]]; then
    printf '%s\n' '缺少 opensandbox-server；请先安装固定版本 0.2.2。' >&2
    exit 1
fi
server_python="$(sed -n '1s/^#!//p' "${server_executable}")"
installed_version="$("${server_python}" -c \
    'import importlib.metadata as metadata; print(metadata.version("opensandbox-server"))')"
if [[ "${installed_version}" != "${EXPECTED_SERVER_VERSION}" ]]; then
    printf 'opensandbox-server 版本不匹配：期望 %s，实际 %s\n' \
        "${EXPECTED_SERVER_VERSION}" "${installed_version}" >&2
    exit 1
fi
if [[ -z "${OPENSANDBOX_SERVER_API_KEY:-}" ]]; then
    printf '%s\n' \
        '必须设置 OPENSANDBOX_SERVER_API_KEY，并把同一值配置给 Worker 的 AGENT_RESEARCH_SANDBOX_API_KEY。' >&2
    exit 1
fi

mkdir -p "${PROJECT_ROOT}/backend/data"
cd "${PROJECT_ROOT}"
exec "${server_executable}" --config "${CONFIG_FILE}"
