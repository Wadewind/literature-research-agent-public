#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
WEB_DIR="${PROJECT_ROOT}/web"
COMPOSE_FILE="${PROJECT_ROOT}/deploy/compose/compose.yml"
ENV_FILE="${AGENT_ENV_FILE:-${PROJECT_ROOT}/.env}"
MODE="fake"

usage() {
    printf '%s\n' \
        "用法: ./scripts/dev.sh [--fake|--real]" \
        "" \
        "  --fake  使用 Fake Parser/Embedding/Chat（默认，不联网）" \
        "  --real  从 .env 读取配置，并启用 Docling 与真实 Provider"
}

case "${1:-}" in
    ""|--fake)
        MODE="fake"
        ;;
    --real)
        MODE="real"
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

for command_name in docker npm; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        printf '缺少命令: %s\n' "${command_name}" >&2
        exit 1
    fi
done

if [[ ! -x "${BACKEND_DIR}/.venv/bin/alembic" ]] \
    || [[ ! -x "${BACKEND_DIR}/.venv/bin/uvicorn" ]] \
    || [[ ! -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
    printf '后端依赖未准备，请先执行: cd backend && uv sync\n' >&2
    exit 1
fi

if [[ ! -d "${WEB_DIR}/node_modules" ]]; then
    printf '前端依赖未准备，请先执行: cd web && npm install\n' >&2
    exit 1
fi

if [[ "${MODE}" == "real" && -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
elif [[ "${MODE}" == "real" ]]; then
    printf '真实模式需要配置文件: %s\n' "${ENV_FILE}" >&2
    exit 1
fi

if [[ "${MODE}" == "real" ]]; then
    export AGENT_PARSER_BACKEND="docling"
    export AGENT_EMBEDDING_BACKEND="openai_compatible"
    export AGENT_CHAT_BACKEND="openai_compatible"
    for variable_name in \
        AGENT_EMBEDDING_BASE_URL \
        AGENT_EMBEDDING_API_KEY \
        AGENT_EMBEDDING_MODEL \
        AGENT_EMBEDDING_DIMENSIONS \
        AGENT_CHAT_BASE_URL \
        AGENT_CHAT_API_KEY \
        AGENT_CHAT_MODEL; do
        if [[ -z "${!variable_name:-}" ]]; then
            printf '真实模式缺少配置: %s\n' "${variable_name}" >&2
            exit 1
        fi
    done
    if [[ "${AGENT_EMBEDDING_DIMENSIONS}" != "1024" ]]; then
        printf 'AGENT_EMBEDDING_DIMENSIONS 必须为 1024（与 pgvector 列一致）\n' >&2
        exit 1
    fi
else
    export AGENT_PARSER_BACKEND="fake"
    export AGENT_EMBEDDING_BACKEND="fake"
    export AGENT_CHAT_BACKEND="fake"
fi

printf '启动模式: %s\n' "${MODE}"
printf '启动 PostgreSQL 与 Valkey...\n'
env -u AGENT_EMBEDDING_API_KEY -u AGENT_CHAT_API_KEY \
    docker compose -f "${COMPOSE_FILE}" up -d --wait postgres valkey

printf '执行数据库迁移...\n'
(
    cd "${BACKEND_DIR}"
    env -u AGENT_EMBEDDING_API_KEY -u AGENT_CHAT_API_KEY \
        .venv/bin/alembic upgrade head
)

child_pids=()

cleanup() {
    local process_id
    trap - EXIT INT TERM
    for process_id in "${child_pids[@]:-}"; do
        if kill -0 "${process_id}" 2>/dev/null; then
            kill "${process_id}" 2>/dev/null || true
        fi
    done
    wait "${child_pids[@]:-}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

printf '启动 Worker...\n'
(
    cd "${BACKEND_DIR}"
    exec .venv/bin/python -m literature_agent.worker
) &
child_pids+=("$!")

# Provider Key 只需要进入 Worker。Worker 已继承当前环境，父进程随后立即移除 Key，
# 避免 API 与前端进程无必要地持有真实凭据。
unset AGENT_EMBEDDING_API_KEY AGENT_CHAT_API_KEY

printf '启动 API: http://127.0.0.1:8000\n'
(
    cd "${BACKEND_DIR}"
    exec .venv/bin/uvicorn literature_agent.main:create_app \
        --factory --host 127.0.0.1 --port 8000
) &
child_pids+=("$!")

printf '启动 Web: http://localhost:5173\n'
(
    cd "${WEB_DIR}"
    exec npm run dev
) &
child_pids+=("$!")

printf '按 Ctrl-C 停止 API、Worker 和 Web；PostgreSQL/Valkey 将继续运行。\n'
set +e
wait -n "${child_pids[@]}"
exit_code=$?
set -e
exit "${exit_code}"
