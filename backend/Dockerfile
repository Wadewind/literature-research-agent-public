# 后端运行时镜像：API 与 Worker 共用，仅安装生产依赖
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app

# 先复制锁文件安装依赖，再复制源码，利用镜像层缓存
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "--no-sync", "python", "-m", "literature_agent.worker"]
