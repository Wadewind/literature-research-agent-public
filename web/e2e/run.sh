#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$WEB_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_DIR/deploy/compose/e2e.yml"
COMPOSE_PROJECT="agent-service-e2e"

export AGENT_STORAGE_ROOT
AGENT_STORAGE_ROOT="$(mktemp -d /tmp/agent-service-e2e-storage.XXXXXX)"
export AGENT_PARSER_BACKEND="fake"
export AGENT_OUTBOX_POLL_INTERVAL_SECONDS="0.05"
export VITE_API_PROXY_TARGET="http://127.0.0.1:18000"

WORKER_PID=""

cleanup() {
  if [[ -n "$WORKER_PID" ]]; then
    kill -TERM "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
  docker compose --project-name "$COMPOSE_PROJECT" --file "$COMPOSE_FILE" down --volumes
  if [[ "$AGENT_STORAGE_ROOT" == /tmp/agent-service-e2e-storage.* ]]; then
    rm -rf "$AGENT_STORAGE_ROOT"
  fi
}
trap cleanup EXIT INT TERM

docker compose --project-name "$COMPOSE_PROJECT" --file "$COMPOSE_FILE" up --detach --wait

POSTGRES_ENDPOINT="$(docker compose --project-name "$COMPOSE_PROJECT" --file "$COMPOSE_FILE" port postgres 5432)"
VALKEY_ENDPOINT="$(docker compose --project-name "$COMPOSE_PROJECT" --file "$COMPOSE_FILE" port valkey 6379)"
export AGENT_DATABASE_URL="postgresql+psycopg://agent:agent@${POSTGRES_ENDPOINT}/agent_service_e2e"
export AGENT_REDIS_URL="redis://${VALKEY_ENDPOINT}/0"

(
  cd "$REPO_DIR/backend"
  uv run alembic upgrade head
  uv run python -m literature_agent.worker
) &
WORKER_PID="$!"

cd "$WEB_DIR"
npx playwright test "$@"
