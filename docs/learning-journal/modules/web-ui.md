# 最小 Web UI（切片 10）

## 解决的问题

Phase 1 前九个切片只有 API 与 Worker，没有用户可见界面。本模块提供最小演示闭环：创建 Project、上传 PDF（幂等）、SSE 实时跟随 Run 进度、取消 Run、按文档结构预览 Element 并定位到来源 PDF 页码。

## 边界和执行流程

```text
浏览器（React SPA，Vite dev server :5173）
  │  /api → Vite proxy → FastAPI :8000（开发期免 CORS）
  ├─ REST：projects / papers / paper-files / runs / document / elements / file
  └─ SSE：GET /runs/{id}/events/stream（原生 EventSource）
```

- 前端不持有任何业务事实：列表与状态全部来自 PostgreSQL 支撑的 REST API；SSE 事件流由后端从 PostgreSQL 重放/推送（见 `run-event.md`）。
- 上传幂等键由浏览器生成：选择新文件时 `crypto.randomUUID()` 生成新 Key，同一文件（同名同大小）重试复用同一 Key（`src/library/uploadIntent.ts`）。
- PDF 预览不做自渲染：`<iframe src=".../file#page=N">` 使用浏览器原生 PDF 查看器的页码锚点，零新增依赖。

## 关键决定与替代方案

- **原生 EventSource 而非 polyfill/fetch 流**：浏览器自动在重连时携带 `Last-Event-ID`（已收到的最大 sequence），与后端 sequence 游标契约天然对齐。代价：只能 GET、不能自定义 Header（当前 dev-user 认证不需要）。
- **终态主动收束**：后端在 Run 终态后关闭流，但 EventSource 对"正常关闭"也会自动重连，造成空转重放。前端在收到终态事件后主动 `close()`，并以 2s 轮询 `GET /runs/{id}` 兜底状态字段（SSE 只携带事件）。注意 Ingestion Run 的成功终态事件是 `result_committed`（与 Run → SUCCEEDED 同事务，切片 6 契约），`run_completed` 只在通用 `RunService.complete_run` 路径出现——终态清单必须两者都含（冒烟中实测修正）。替代方案（服务端发送特殊 done 帧）会增加协议复杂度，未采用。
- **具名事件逐个订阅**：后端 SSE 帧带 `event: <type>` 字段，EventSource 的 `onmessage` 收不到具名事件，必须按类型 `addEventListener`。类型清单集中在 `src/runs/eventStore.ts` 的 `KNOWN_EVENT_TYPES`，新增事件类型需同步——这是与后端的显式耦合点。
- **事件归并幂等**：重连重放会产生重复 sequence，`applyEvent` 按 sequence 去重并保持升序（`src/runs/eventStore.ts`），与后端"不重不漏"语义对齐。
- **iframe `#page=N` 而非 pdf.js**：满足"Element 定位来源页码"的最小实现；bbox 高亮需要 pdf.js 自渲染，留作后续升级。点击 Element 时以 `key={page}` 重建 iframe，规避原生查看器对同 URL 片段变化不响应的问题。
- **后端补齐两个只读端点**：`GET .../papers`（Paper + 最新 Version 摘要）与 `GET .../paper-versions/{id}/file`（inline PDF）。`file` 端点不要求已有 Parse Revision（上传成功即可预览原文），所有权链校验与 document 查询一致，越权一律 404。
- **`paper_versions.display_filename` 迁移**：文件名此前只存在于 Run `input_payload`，无法支撑文献库列表展示；作为 Version 的展示字段落库（仅为展示信息，不参与存储路径）。

## 失败、重试与取消行为

- 上传失败（400 非 PDF/超限、409 幂等冲突、413）在表单下方展示后端 detail；
- 404（越权/不存在）统一展示"资源不存在或无权访问"，不泄漏所有权；
- Run FAILED 时页面展示错误摘要并保留完整事件时间线；
- 取消按钮仅在 `queued`/`running`/`retry_wait` 可用；`cancel_requested` 后按钮消失、轮询直至 `cancelled`；
- SSE 断线由 EventSource 自动重连 + Last-Event-ID 重放，同时每次 error 事件触发一次 Run 查询刷新兜底。

## 测试与运行结果

- Vitest（35 passed，node 环境，纯状态逻辑）：
  - `eventStore`：乱序/重复事件去重排序、三类终态事件触发收束、终态后忽略重复；
  - `runStatus`：终态表（succeeded/failed/cancelled）与可取消表（queued/running/retry_wait）；
  - `uploadIntent`：同文件复用 Key、改名/改大小生成新 Key；
  - `client`：ApiError 解析与 404/400/409/413 文案映射。
- `npm run build`（tsc strict + vite build）通过。
- 手动闭环（本地 postgres/valkey + uvicorn + 本地 Worker，真实 Docling 解析）：上传 `text_two_pages.pdf` → SSE 实时事件 → succeeded → papers 列表 `parse_ready=true` → file 端点字节与原件一致（inline disposition）→ SSE `Last-Event-ID: 2` 正确从 sequence 3 重放；queued 状态取消 → cancelled；终态取消 → 409；Playwright 截图验证 4 个页面与 404 呈现。

## 代码入口

- 页面：`web/src/pages/`（ProjectsPage、LibraryPage、RunDetailPage、DocumentPage）
- SSE：`web/src/runs/useRunEvents.ts`、`eventStore.ts`、`runStatus.ts`
- API 封装：`web/src/api/client.ts`、`types.ts`
- 后端新端点：`backend/src/literature_agent/api/papers.py`、`application/paper_query_service.py`

## 已知限制

- Element 仅定位到页码，不做 bbox 高亮；点击切换页码会重建 iframe（整份 PDF 重新加载）。
- Element 列表固定拉取前 200 条，无虚拟滚动/分页 UI。
- 具名 SSE 事件类型清单需与后端手工同步。
- 仅开发代理（Vite proxy），无生产构建部署与 CORS 配置（切片 11 Compose Smoke 再定）。
- 无 Playwright E2E（切片 11）。

## 60 秒面试说明

这个 UI 演示了"不可靠实时通道 + 可靠事实来源"的前端搭档模式：SSE 只是把事件推给浏览器的通道，所有状态都以 PostgreSQL 为事实来源——断线重连用浏览器原生 EventSource 自带的 Last-Event-ID 重放，前端归并逻辑按 sequence 去重排序，所以重放不重不漏；终态后前端主动关闭流，避免对已结束的 Run 无限重连。上传用浏览器生成的 Idempotency-Key 保证重试安全。PDF 预览刻意用浏览器原生查看器的 `#page=N` 锚点而不是引入 pdf.js，用零依赖满足"从结构化 Element 回到来源页码"的闭环。
