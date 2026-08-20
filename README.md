# Literature Review Agent System

面向学习和简历展示的文献综述 Agent 系统。Phase 1（Project、个人文献库与可靠异步导入）的核心功能已完成：同一用户的 PDF 按内容哈希只存储和解析一次，可被多个 Project 收录；后台提供可恢复导入、Event/SSE 和结构化文档查询，Web 端提供完整演示闭环。切片 11 验收复盘仍保留为后续工作。

## 仓库布局

```text
agent-service/
├─ backend/   # Python、FastAPI、Worker、数据库迁移与后端测试
├─ web/       # React/Vite 前端
├─ docs/      # 总体规范、阶段 Spec、学习笔记与决策
└─ deploy/    # 本地部署配置（postgres/valkey Compose）
```

## 本地运行

需要 4 个进程：postgres/valkey 用容器，API、Worker 和前端在本地运行。

```bash
# 终端 1：数据库与队列（容器，可长期运行）
docker compose -f deploy/compose/compose.yml up -d postgres valkey

# 终端 2：数据库迁移（全新数据库或拉取新迁移后执行一次）
cd backend && .venv/bin/alembic upgrade head

# 终端 3：API（http://127.0.0.1:8000）
cd backend && .venv/bin/uvicorn literature_agent.main:create_app --factory --host 127.0.0.1 --port 8000

# 终端 4：Worker（HF_HUB_OFFLINE=1 强制使用已缓存的 Docling 模型，
# 避免本机代理环境变量导致 HuggingFace 下载走 SOCKS 报错；
# 首次运行或缓存缺失时去掉该变量以下载模型）
cd backend && HF_HUB_OFFLINE=1 .venv/bin/python -m literature_agent.worker

# 终端 5：前端（http://localhost:5173，/api 由 Vite proxy 转发到 8000）
cd web && npm install && npm run dev
```

首次准备：`cd backend && uv sync`。普通后端测试（pytest 默认套件）不需要真实模型与付费 API。

阶段目标和学习顺序见 `docs/learning-journal/phases/`。
