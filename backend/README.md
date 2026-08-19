# Backend

Python 3.13 backend for the literature review Agent system. Dependencies and commands are managed from this directory with uv.

```bash
cd backend
uv sync
uv run agent-service
```

The service is built through Phase 1 vertical slices. The current minimal slice exposes a FastAPI application factory, lifespan-managed application state, and `GET /health/live`. Databases, queues, and worker infrastructure are introduced in later slices.

