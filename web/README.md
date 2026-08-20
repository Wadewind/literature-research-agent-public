# Web

Phase 1 的 Web 工作台：Project 创建、个人文献库、Project 收录管理、PDF 上传与内容哈希自动复用、Run 进度 SSE 跟随与取消、Element 结构预览与来源 PDF 页码定位。

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
```

Project 中的“移出”只删除收录关系，原 PDF 和解析结果仍保留在个人文献库。Playwright 固化 E2E 仍属于切片 11。
