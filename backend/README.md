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
- Project CRUD API (`POST /api/v1/projects`, `GET /api/v1/projects`, `GET /api/v1/projects/{project_id}`)
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

启动 PostgreSQL、Valkey（及可选的 worker 服务）：

```bash
cd deploy/compose
docker compose up -d            # postgres + valkey + worker
docker compose up -d postgres valkey   # 只启动基础设施，worker 在宿主机运行
```

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
