#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
WEB_DIR="${PROJECT_ROOT}/web"
COMPOSE_FILE="${PROJECT_ROOT}/deploy/compose/compose.yml"
ENV_FILE="${AGENT_ENV_FILE:-${PROJECT_ROOT}/.env}"
MODE="fake"
START_OPENSANDBOX="false"

drop_unsupported_socks_proxies() {
    local proxy_name
    local proxy_value
    local proxy_cleared="false"

    if "${BACKEND_DIR}/.venv/bin/python" -c 'import socksio' >/dev/null 2>&1; then
        return
    fi
    for proxy_name in ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do
        proxy_value="${!proxy_name:-}"
        case "${proxy_value}" in
            socks5://*|socks5h://*)
                unset "${proxy_name}"
                proxy_cleared="true"
                ;;
        esac
    done
    if [[ "${proxy_cleared}" == "true" ]]; then
        printf '%s\n' \
            '警告: 检测到 SOCKS 代理但虚拟环境未安装 socksio；已为本次启动清除 SOCKS 代理变量。' \
            '如网络必须经过 SOCKS，请先显式安装并锁定 socksio，再重新启动。' >&2
    fi
}

usage() {
    printf '%s\n' \
        "用法: ./scripts/dev.sh [--fake|--real]" \
        "" \
        "  --fake  使用 Fake Parser/Embedding/Chat/arXiv（默认，不联网）" \
        "  --real  从 .env 读取配置，并启用 Docling、真实 Provider 与 arXiv；deep_agents 模式同时启动 OpenSandbox"
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

for command_name in docker npm setsid curl; do
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
    export AGENT_ARXIV_BACKEND="httpx"
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
    if [[ "${AGENT_RESEARCH_RUNTIME_BACKEND:-fake}" == "deep_agents" ]]; then
        if [[ "${AGENT_RESEARCH_SANDBOX_DOMAIN:-127.0.0.1:8080}" != "127.0.0.1:8080" ]] \
            || [[ "${AGENT_RESEARCH_SANDBOX_PROTOCOL:-http}" != "http" ]]; then
            printf '%s\n' \
                '一键启动 OpenSandbox 只支持项目固定的 http://127.0.0.1:8080；远程 Server 请使用手动启动流程。' >&2
            exit 1
        fi
        if [[ -z "${AGENT_RESEARCH_SANDBOX_API_KEY:-}" ]]; then
            printf '%s\n' \
                'deep_agents 模式缺少配置: AGENT_RESEARCH_SANDBOX_API_KEY' >&2
            exit 1
        fi
        if [[ -n "${OPENSANDBOX_SERVER_API_KEY:-}" ]] \
            && [[ "${OPENSANDBOX_SERVER_API_KEY}" != "${AGENT_RESEARCH_SANDBOX_API_KEY}" ]]; then
            printf '%s\n' \
                'OPENSANDBOX_SERVER_API_KEY 与 AGENT_RESEARCH_SANDBOX_API_KEY 不一致。' >&2
            exit 1
        fi
        START_OPENSANDBOX="true"
    fi
    drop_unsupported_socks_proxies
else
    export AGENT_PARSER_BACKEND="fake"
    export AGENT_EMBEDDING_BACKEND="fake"
    export AGENT_CHAT_BACKEND="fake"
    export AGENT_ARXIV_BACKEND="fake"
    export AGENT_RESEARCH_RUNTIME_BACKEND="fake"
fi

printf '启动模式: %s\n' "${MODE}"
printf '启动 PostgreSQL 与 Valkey...\n'
env -u AGENT_EMBEDDING_API_KEY -u AGENT_CHAT_API_KEY -u AGENT_RESEARCH_MODEL_API_KEY \
    docker compose -f "${COMPOSE_FILE}" up -d --wait postgres valkey

printf '执行数据库迁移...\n'
(
    cd "${BACKEND_DIR}"
    env -u AGENT_EMBEDDING_API_KEY -u AGENT_CHAT_API_KEY -u AGENT_RESEARCH_MODEL_API_KEY \
        .venv/bin/alembic upgrade head
)

child_pids=()

wait_for_api_ready() {
    local api_pid="$1"
    local attempt
    for attempt in {1..80}; do
        if ! kill -0 "${api_pid}" 2>/dev/null; then
            printf 'API 进程在就绪前退出。\n' >&2
            return 1
        fi
        if curl --noproxy '*' --fail --silent --max-time 1 \
            http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
            printf 'API 已就绪。\n'
            return 0
        fi
        sleep 0.25
    done
    printf 'API 在 20 秒内未就绪，停止其余本地服务。\n' >&2
    return 1
}

wait_for_opensandbox_ready() {
    local opensandbox_pid="$1"
    local attempt
    for attempt in {1..80}; do
        if ! kill -0 "${opensandbox_pid}" 2>/dev/null; then
            printf 'OpenSandbox Server 在就绪前退出。\n' >&2
            return 1
        fi
        if curl --noproxy '*' --fail --silent --max-time 1 \
            http://127.0.0.1:8080/health >/dev/null 2>&1; then
            printf 'OpenSandbox Server 已就绪。\n'
            return 0
        fi
        sleep 0.25
    done
    printf 'OpenSandbox Server 在 20 秒内未就绪，停止其余本地服务。\n' >&2
    return 1
}

cleanup() {
    local process_id
    trap - EXIT INT TERM
    for process_id in "${child_pids[@]:-}"; do
        # 每个服务由 setsid 建立独立进程组；负 PID 同时终止 npm 与其 Vite 子进程。
        kill -TERM -- "-${process_id}" 2>/dev/null || true
    done
    wait "${child_pids[@]:-}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

if [[ "${START_OPENSANDBOX}" == "true" ]]; then
    printf '启动 OpenSandbox Server: http://127.0.0.1:8080\n'
    (
        exec setsid env \
            -u AGENT_EMBEDDING_API_KEY \
            -u AGENT_CHAT_API_KEY \
            -u AGENT_RESEARCH_MODEL_API_KEY \
            -u AGENT_RESEARCH_SANDBOX_API_KEY \
            OPENSANDBOX_SERVER_API_KEY="${AGENT_RESEARCH_SANDBOX_API_KEY}" \
            "${PROJECT_ROOT}/scripts/opensandbox-server.sh"
    ) &
    opensandbox_pid="$!"
    child_pids+=("${opensandbox_pid}")
    # Server 已获得独立环境副本；避免其专用变量继续进入 Worker/API/Web。
    unset OPENSANDBOX_SERVER_API_KEY
    printf '等待 OpenSandbox Server 就绪...\n'
    wait_for_opensandbox_ready "${opensandbox_pid}"
else
    # Fake Agent 不需要 Sandbox Key，避免无关进程继承本地凭据。
    unset AGENT_RESEARCH_SANDBOX_API_KEY OPENSANDBOX_SERVER_API_KEY
fi

printf '启动 Worker...\n'
(
    cd "${BACKEND_DIR}"
    exec setsid .venv/bin/python -m literature_agent.worker
) &
child_pids+=("$!")
worker_metrics_port="${AGENT_WORKER_METRICS_PORT:-8001}"
if [[ "${worker_metrics_port}" == "0" ]]; then
    printf 'Worker Metrics: 已禁用（AGENT_WORKER_METRICS_PORT=0）\n'
else
    printf 'Worker Metrics: http://127.0.0.1:%s/metrics\n' "${worker_metrics_port}"
fi

# Provider Key 只需要进入 Worker。Worker 已继承当前环境，父进程随后立即移除 Key，
# 避免 API 与前端进程无必要地持有真实凭据。
unset AGENT_EMBEDDING_API_KEY AGENT_CHAT_API_KEY AGENT_RESEARCH_MODEL_API_KEY

printf '启动 API: http://127.0.0.1:8000\n'
printf 'API Metrics: http://127.0.0.1:8000/metrics\n'
(
    cd "${BACKEND_DIR}"
    exec setsid .venv/bin/uvicorn literature_agent.main:create_app \
        --factory --host 127.0.0.1 --port 8000 --no-access-log
) &
api_pid="$!"
child_pids+=("${api_pid}")

# Browser Control 使 API 也需要 Sandbox Key；API 启动后移除父进程副本，避免 Web 继承。
unset AGENT_RESEARCH_SANDBOX_API_KEY

printf '等待 API 就绪...\n'
wait_for_api_ready "${api_pid}"

printf '启动 Web: http://localhost:5173\n'
(
    cd "${WEB_DIR}"
    exec setsid npm run dev
) &
child_pids+=("$!")

if [[ "${START_OPENSANDBOX}" == "true" ]]; then
    printf '按 Ctrl-C 停止 OpenSandbox、API、Worker 和 Web；PostgreSQL/Valkey 将继续运行。\n'
else
    printf '按 Ctrl-C 停止 API、Worker 和 Web；PostgreSQL/Valkey 将继续运行。\n'
fi
set +e
wait -n "${child_pids[@]}"
exit_code=$?
set -e
exit "${exit_code}"
