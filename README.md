# Literature Review Agent System

面向学习和简历展示的文献综述 Agent 系统。Phase 1–3 已完成：系统支持 Project 与个人文献库、可靠
异步 PDF 导入、pgvector 混合检索、带 Evidence/PDF 页码引用的 RAG 问答，以及可暂停恢复、人工确认
Outline 并导出引用 Artifact 的固定 Review Workflow。Phase 4 将其收束为本地可复现、可评测的
Demo-ready Core v1；该里程碑不代表公网生产、登录认证、备份恢复、永久删除/GC 或 SLA 已完成。

## 仓库布局

```text
agent-service/
├─ backend/   # Python、FastAPI、Worker、数据库迁移与后端测试
├─ web/       # React/Vite 前端
├─ docs/      # 总体规范、阶段 Spec、学习笔记与决策
├─ deploy/    # PostgreSQL/Valkey Compose
└─ scripts/   # 本地开发启动脚本
```

## 首次准备

需要 Python 3.13、uv、Node.js/npm、Docker Compose。

```bash
cd backend
uv sync

cd ../web
npm install
```

复制无密钥模板创建本地配置：

```bash
cd /home/xubin/Projects/agent-service
cp .env.example .env
chmod 600 .env
```

`.env` 已被 Git 忽略，不会自动提交。应用本身不使用 dotenv；一键脚本会读取它，手动启动时需自行 `source`。

## 一键启动

默认 Fake 模式同时选择仓库内版本化的 Parser、Embedding、Chat 和 arXiv Fixture，
不读取 `.env`、不联网、不产生模型费用：

```bash
./scripts/dev.sh
# 等价于 ./scripts/dev.sh --fake
```

真实模式读取根目录 `.env`，启用 Docling、真实 Embedding/Chat 和官方 arXiv：

```bash
./scripts/dev.sh --real
```

脚本会启动 PostgreSQL/Valkey、执行 Alembic 迁移，再并行启动 API、Worker 和 Web。按 `Ctrl-C` 停止 API、Worker、npm 及其 Vite 子进程；数据库和 Valkey 容器继续运行。脚本不会安装依赖、删除数据卷或打印 API Key；Fake 模式也不会读取 `.env`。

访问地址：

- Web：<http://localhost:5173>
- API：<http://127.0.0.1:8000>
- 健康检查：<http://127.0.0.1:8000/health/ready>

Fake arXiv 使用 `review-demo.v1` 合成语料：固定返回 4 条版本化来源，其中 3 条可离线导入，1 条稳定
模拟 PDF 不可用。Fake Parser、Embedding 与 Chat 会继续完成解析、索引、Evidence Matrix 和 Review；
Matrix 有明确的证据不足行，Outline 可以先提交 feedback 再 approve，从而重复演示第二次 interrupt。
Fake Chunk/RAG 使用无需下载词表的版本化 tokenizer；manifest 会按 size/SHA-256 校验 PDF，避免语料
静默漂移。Fixture 位于 `backend/src/literature_agent/infrastructure/fixtures/review/v1/`，不包含真实论文
或用户数据。

如需使用其他配置文件：

```bash
AGENT_ENV_FILE=/path/to/provider.env ./scripts/dev.sh --real
```

## 真实 Parser 与 Provider

真实模式的关键配置如下，完整模板见 `.env.example`：

```bash
AGENT_PARSER_BACKEND=docling

AGENT_EMBEDDING_BACKEND=openai_compatible
AGENT_EMBEDDING_BASE_URL=https://open.bigmodel.cn/api/paas/v4
AGENT_EMBEDDING_API_KEY=...
AGENT_EMBEDDING_MODEL=embedding-3
AGENT_EMBEDDING_DIMENSIONS=1024

AGENT_CHAT_BACKEND=openai_compatible
AGENT_CHAT_BASE_URL=https://api.deepseek.com
AGENT_CHAT_API_KEY=...
AGENT_CHAT_MODEL=deepseek-v4-flash
AGENT_CHAT_JSON_SCHEMA_SUPPORTED=false

AGENT_ARXIV_BACKEND=httpx
```

- Embedding Base URL 是 API 根路径；Adapter 会自行追加 `/embeddings`。
- 不支持 `response_format=json_schema` 的 Chat Provider 必须设置 `AGENT_CHAT_JSON_SCHEMA_SUPPORTED=false`，业务输出仍会经过 Pydantic Schema 和 Citation Validator。
- Docling 主路径失败时只对结构性 PDF 错误降级到 pypdf；OCR 默认关闭。
- Docling 首次运行需要下载模型。缓存准备后可在 `.env` 设置 `HF_HUB_OFFLINE=1`；缓存缺失时不要设置。
- 若真实模式检测到 SOCKS 代理但虚拟环境未安装 `socksio`，一键脚本会明确告警，并仅为本次启动清除无法使用的 SOCKS 代理变量；不会修改系统设置。若网络必须经过 SOCKS，需要显式安装并锁定 `socksio`。手动启动 Worker 时则需自行处理代理环境。

真实 Provider 只在 Worker 内调用。API 与 Worker 必须使用同一 `AGENT_STORAGE_ROOT`；推荐都从 `backend/` 目录启动。

## 手动启动

需要 PostgreSQL、Valkey、API、Worker 和 Web 五个服务：前两者用容器，后三者在宿主机运行。不要混用宿主机 API 与 Compose `worker`，两者默认 Storage 不同。

```bash
# 终端 1：基础设施
docker compose -f deploy/compose/compose.yml up -d --wait postgres valkey

# 终端 2：迁移和 API
cd backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn literature_agent.main:create_app --factory --host 127.0.0.1 --port 8000

# 终端 3：Worker（Fake）
cd backend
AGENT_PARSER_BACKEND=fake \
AGENT_EMBEDDING_BACKEND=fake \
AGENT_CHAT_BACKEND=fake \
AGENT_ARXIV_BACKEND=fake \
.venv/bin/python -m literature_agent.worker

# 终端 4：Web
cd web
npm run dev
```

手动启动真实 Worker：

```bash
cd backend
set -a
source ../.env
set +a
export AGENT_PARSER_BACKEND=docling
export AGENT_EMBEDDING_BACKEND=openai_compatible
export AGENT_CHAT_BACKEND=openai_compatible
export AGENT_ARXIV_BACKEND=httpx
.venv/bin/python -m literature_agent.worker
```

## 停止基础设施

```bash
docker compose -f deploy/compose/compose.yml stop postgres valkey
```

只有确定要删除本地开发数据库、队列和全部卷时才执行：

```bash
docker compose -f deploy/compose/compose.yml down -v
```

## 测试

普通测试使用 Fake Provider，不联网：

```bash
cd backend
.venv/bin/pytest tests -q --ignore=tests/integration
.venv/bin/pytest tests/integration -q
.venv/bin/ruff check src tests
.venv/bin/pyright

cd ../web
npm test
npm run build
npm run test:e2e
```

真实组件测试必须显式启用：

```bash
cd backend
AGENT_RUN_DOCLING_TESTS=1 .venv/bin/pytest tests/infrastructure/test_docling_parser.py -q

set -a
source ../.env
set +a
AGENT_RUN_PROVIDER_TESTS=1 .venv/bin/pytest tests/infrastructure/test_provider_smoke.py -q
```

阶段目标、实现契约和实际验收结果见 `docs/learning-journal/phases/`；模块边界与已知限制见 `docs/learning-journal/modules/`。
