# Phase 1 Web UI

## 解决的问题

本模块提供 Phase 1 的用户可见闭环：创建 Project、浏览 owner 个人文献库、向 Project 上传或直接收录已有论文、移除收录关系、通过 SSE 跟随和取消 Run，以及按文档结构预览 Element 并定位到来源 PDF 页码。

## 边界和执行流程

```text
浏览器（React SPA，Vite dev server :5173）
  │  /api → Vite proxy → FastAPI :8000（开发期免 CORS）
  ├─ REST：projects / library / project-papers / paper-files / runs / document / elements / file
  └─ SSE：GET /runs/{id}/events/stream（原生 EventSource）
```

- 前端不持有任何业务事实：列表与状态全部来自 PostgreSQL 支撑的 REST API；SSE 事件流由后端从 PostgreSQL 重放/推送（见 `run-event.md`）。
- `/library` 展示 owner 范围的个人文献资产及其 Project 收录范围；Project 页面并行读取当前收录与个人库，可直接收录已有 PaperVersion。移出 Project 只删除 `ProjectPaper`，不会删除 PDF 或解析结果。
- Project 列表使用 `ProjectPaper.selected_version_id` 固定的 Version，不使用“最新 Version”隐式切换语料；该边界会直接被 Phase 2 Retrieval 继承。
- 上传幂等键由浏览器生成：选择新文件时 `crypto.randomUUID()` 生成新 Key，同一文件（同名同大小）重试复用同一 Key（`src/library/uploadIntent.ts`）。
- 上传响应区分新建、复用和已收录：新文件带 `run_id` 进入 Run 页；复用已解析文件时 `run_id=null` 并直接刷新文献库。
- PDF 预览不做自渲染：`<iframe src=".../file#page=N">` 使用浏览器原生 PDF 查看器的页码锚点，零新增依赖。

## 关键决定与替代方案

- **原生 EventSource 而非 polyfill/fetch 流**：浏览器自动在重连时携带 `Last-Event-ID`（已收到的最大 sequence），与后端 sequence 游标契约天然对齐。代价：只能 GET、不能自定义 Header（当前 dev-user 认证不需要）。
- **终态主动收束**：后端在 Run 终态后关闭流，但 EventSource 对“正常关闭”也会自动重连。前端收到 `result_committed`、`run_completed`、`run_failed` 或 `run_cancelled` 后主动 `close()`，并以 2s 轮询 `GET /runs/{id}` 兜底。`run_cancel_requested` 是非终态事件，只更新页面并保持 SSE 打开。
- **具名事件逐个订阅**：后端 SSE 帧带 `event: <type>` 字段，EventSource 的 `onmessage` 收不到具名事件，必须按类型 `addEventListener`。类型清单集中在 `src/runs/eventStore.ts` 的 `KNOWN_EVENT_TYPES`，新增事件类型需同步——这是与后端的显式耦合点。
- **事件归并幂等**：重连重放会产生重复 sequence，`applyEvent` 按 sequence 去重并保持升序（`src/runs/eventStore.ts`），与后端"不重不漏"语义对齐。
- **iframe `#page=N` 而非 pdf.js**：满足"Element 定位来源页码"的最小实现；bbox 高亮需要 pdf.js 自渲染，留作后续升级。点击 Element 时以 `key={page}` 重建 iframe，规避原生查看器对同 URL 片段变化不响应的问题。
- **读模型显式返回固定 Version**：个人库与 Project 文献列表都返回非空 `version`；Project 列表中的 Version 来自 `ProjectPaper.selected_version_id`。`GET .../paper-versions/{id}/file` 不要求已有 Parse Revision，但要求当前 Project 确实收录该 Version，越权一律 404。
- **`paper_versions.display_filename` 迁移**：文件名此前只存在于 Run `input_payload`，无法支撑文献库列表展示；作为 Version 的展示字段落库（仅为展示信息，不参与存储路径）。

## 失败、重试与取消行为

- 上传失败（当前后端的非 PDF/超限均为 400，幂等冲突为 409；代理层也可能返回 413）在表单下方展示 detail；
- 404（越权/不存在）统一展示"资源不存在或无权访问"，不泄漏所有权；
- Run FAILED 时页面展示错误摘要并保留完整事件时间线；
- 取消按钮仅在 `queued`/`running`/`retry_wait` 可用；`cancel_requested` 后按钮消失、轮询直至 `cancelled`；
- SSE 断线由 EventSource 自动重连 + Last-Event-ID 重放，同时每次 error 事件触发一次 Run 查询刷新兜底。

## 测试与运行结果

- Vitest（36 passed，node 环境，纯状态逻辑）：
  - `eventStore`：乱序/重复事件去重排序、终态收束、`run_cancel_requested` 保持连接；
  - `runStatus`：终态表（succeeded/failed/cancelled）与可取消表（queued/running/retry_wait）；
  - `uploadIntent`：同文件复用 Key、改名/改大小生成新 Key；
  - `client`：ApiError 解析与 404/400/409/413 文案映射。
- `npm run build`（tsc strict + vite build）通过。
- 手动闭环（本地 postgres/valkey + uvicorn + 本地 Worker，真实 Docling 解析）：上传 `text_two_pages.pdf` → SSE 实时事件 → succeeded → papers 列表 `parse_ready=true` → file 端点字节与原件一致（inline disposition）→ SSE `Last-Event-ID: 2` 正确从 sequence 3 重放；queued 状态取消 → cancelled；终态取消 → 409；Playwright 截图验证 4 个页面与 404 呈现。

## 代码入口

- 页面：`web/src/pages/`（ProjectsPage、PersonalLibraryPage、LibraryPage、RunDetailPage、DocumentPage）
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

这个 UI 同时表达了资源模型和可靠执行模型：Paper 属于个人文献库，Project 通过固定 Version 的关系收录它，因此同一解析结果可跨 Project 复用且不会因新版本静默改变检索语料。运行进度使用“PostgreSQL 事实源 + SSE 低延迟通道”：断线按 Last-Event-ID 重放并按 sequence 幂等归并，取消请求保持连接，真正终态才主动关闭。PDF 预览用浏览器原生 `#page=N` 锚点，以零新增依赖完成从 Element 回到来源页码的闭环。
