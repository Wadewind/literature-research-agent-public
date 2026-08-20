# Backend

Python 3.13 backend for the literature review Agent system. Dependencies and commands are managed from this directory with uv.

```bash
cd backend
uv sync
uv run agent-service
```

The service is built through Phase 1 vertical slices. The current slices expose:

- FastAPI application factory with lifespan-managed application state
- `GET /health/live`
- Project 创建、列表与详情 API（`POST /api/v1/projects`、`GET /api/v1/projects`、`GET /api/v1/projects/{project_id}`）
- Paper file upload API (`POST /api/v1/projects/{project_id}/paper-files`) with idempotency
- owner 级个人文献库（`GET /api/v1/library/papers`）；相同 owner + SHA-256 自动复用 PaperVersion 和解析结果
- Project 收录关系 API（`GET/POST /api/v1/projects/{project_id}/papers`、`DELETE .../papers/{paper_id}`）；移出 Project 不删除个人文献库资产
- Run/Event API (`GET /api/v1/runs/{run_id}`, `POST /api/v1/runs/{run_id}/cancel`, `GET /api/v1/runs/{run_id}/events?after_sequence=&limit=`) with cursor pagination
- Run event SSE stream (`GET /api/v1/runs/{run_id}/events/stream`) with `Last-Event-ID` resume, 15s heartbeat comments and terminal close; Valkey Pub/Sub lowers latency while 1s DB polling guarantees convergence
- Document query API (`GET /api/v1/projects/{project_id}/paper-versions/{version_id}/document`, `GET .../elements`) with page/section/type filters
- PostgreSQL persistence with SQLAlchemy 2.0 Async and Alembic migrations
- Actor Context with ownership filtering
- Queue Outbox + ARQ Worker reliable dispatch; worker-side Attempt lease/heartbeat (600s/30s) with reconcile loop recovering crashed runs, and error-classified retries (`AGENT_MAX_RUN_ATTEMPTS=3`, `RETRY_WAIT` + outbox redispatch)
- Real PDF parsing via Docling (standard pipeline, OCR off by default) with pypdf degraded fallback; `AGENT_PARSER_BACKEND=fake` switches to the deterministic Fake parser

Run the ARQ worker (dispatches the queue outbox and executes run jobs):

```bash
cd backend
uv run agent-worker  # 或 uv run python -m literature_agent.worker
```

The worker requires PostgreSQL and Valkey; see `deploy/compose/compose.yml`.
Parser timeout defaults to 300s (`AGENT_PARSER_TIMEOUT_SECONDS`). Docling
downloads its layout models on first run; the real-parsing contract tests are
opt-in: `AGENT_RUN_DOCLING_TESTS=1 uv run pytest tests/infrastructure/test_docling_parser.py`.

## 本地开发

从仓库根目录只启动 PostgreSQL 和 Valkey，API 与 Worker 都在宿主机的
`backend/` 目录运行：

```bash
docker compose -f deploy/compose/compose.yml up -d postgres valkey
cd backend
uv run agent-service
# 另一个终端：cd backend && uv run agent-worker
```

当前 Compose 中的 `worker` 使用容器内 `/data/storage` volume，而宿主机 API
默认使用 `backend/data/storage`。在 Compose 尚未同时提供 API 和共享 Storage
配置前，不要把容器 Worker 与宿主机 API 混用，否则 Worker 无法读取上传的 PDF。

执行迁移：

```bash
cd backend
uv run alembic upgrade head
```

运行测试：

```bash
cd backend
uv run pytest
```
