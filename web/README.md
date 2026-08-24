# Web

Phase 1–4 的 Web 工作台：Project 创建、个人文献库、Project 收录管理、PDF 上传与内容哈希自动复用、
Run 进度 SSE 跟随与取消、Element/PDF 来源预览、Project/单篇/多篇范围的带引用 RAG，以及固定
Review 的来源、结构化 Outline HITL、Evidence Matrix、Section/Citation 与 Artifact 下载。

技术栈：React 19、Vite、TypeScript strict、TanStack Query、react-router-dom。无 UI 组件库；SSE 使用原生 `EventSource`（断线重连自动携带 `Last-Event-ID`）；PDF 预览使用浏览器原生查看器的 `#page=N` 锚点。

## 开发

前端依赖本地后端（API :8000 与 Worker）。完整启动步骤（postgres/valkey 容器、迁移、API、Worker、`HF_HUB_OFFLINE` 说明）见根目录 [`README.md`](../README.md#本地运行)。

```bash
npm install
npm run dev   # http://localhost:5173，/api 代理到 127.0.0.1:8000
```

## 质量命令

```bash
npm test        # Vitest：SSE 归并、Run 状态、上传幂等键、错误映射
npm run build   # tsc strict + vite build
npm run test:e2e # 隔离 Compose + API/Worker + Chromium 的 Phase 1–4 核心旅程
```

首次运行 E2E 前执行 `npx playwright install chromium --no-shell`。测试使用动态映射端口、临时 Storage，
并显式选择 Fake Parser/Embedding/Chat/arXiv、清除 Provider Key，不读取 `.env`、不访问实时 arXiv 或
付费模型，也不读写日常开发数据库。失败时保留 screenshot/video/trace；成功后不提交这些临时产物。

Project 中的“移出”只删除收录关系，原 PDF 和解析结果仍保留在个人文献库。
