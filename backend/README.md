# Backend

Python 3.13 backend for the literature review Agent system. Dependencies and commands are managed from this directory with uv.

```bash
cd backend
uv sync
uv run agent-service
```

The service is built through Phase 1 vertical slices. The current slice exposes:

- FastAPI application factory with lifespan-managed application state
- `GET /health/live`
- Project CRUD API (`POST /api/v1/projects`, `GET /api/v1/projects`, `GET /api/v1/projects/{project_id}`)
- PostgreSQL persistence with SQLAlchemy 2.0 Async and Alembic migrations
- Actor Context with ownership filtering

Queues, workers, and ingestion workflows are introduced in later slices.

## 本地开发

启动 PostgreSQL：

```bash
cd deploy/compose
docker compose up -d
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

