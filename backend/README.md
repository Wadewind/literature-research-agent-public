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
- Run/Event API (`GET /api/v1/runs/{run_id}`, `POST /api/v1/runs/{run_id}/cancel`, `GET /api/v1/runs/{run_id}/events`)
- PostgreSQL persistence with SQLAlchemy 2.0 Async and Alembic migrations
- Actor Context with ownership filtering
- Queue Outbox + ARQ Worker reliable dispatch (placeholder executor until slice 6)

Run the ARQ worker (dispatches the queue outbox and executes run jobs):

```bash
cd backend
uv run agent-worker  # 或 uv run python -m literature_agent.worker
```

The worker requires PostgreSQL and Valkey; see `deploy/compose/compose.yml`.

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

