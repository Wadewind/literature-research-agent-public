# Web

Phase 1 切片 10 的最小 Web UI：Project 创建、文献库与 PDF 上传、Run 进度 SSE 跟随与取消、Element 结构预览与来源 PDF 页码定位。

技术栈：React 19、Vite、TypeScript strict、TanStack Query、react-router-dom。无 UI 组件库；SSE 使用原生 `EventSource`（断线重连自动携带 `Last-Event-ID`）；PDF 预览使用浏览器原生查看器的 `#page=N` 锚点。

## 开发

```bash
# 后端（另起终端）：postgres/valkey/api/worker
docker compose -f deploy/compose/compose.yml up -d

# 前端开发服务器（/api 代理到 127.0.0.1:8000）
npm install
npm run dev
```

## 质量命令

```bash
npm test        # Vitest：SSE 归并、Run 状态、上传幂等键、错误映射
npm run build   # tsc strict + vite build
```

Playwright E2E 属于切片 11，此处不包含。
