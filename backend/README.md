# Backend

Python 3.13 backend for the literature review Agent system. Dependencies and commands are managed from this directory with uv.

```bash
cd backend
uv sync
uv run agent-service
```

The current entry point is only a project-layout smoke test. FastAPI and Worker processes will be introduced through Phase 0 vertical slices.

