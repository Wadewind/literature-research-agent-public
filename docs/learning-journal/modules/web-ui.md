# Phase 1–2 Web UI

## 解决的问题

本模块提供 Phase 1–2 的用户可见闭环：管理 Project 与个人文献资产、导入和定位 PDF，并从整个 Project、单篇或多篇论文进入带引用的 RAG 对话；回答可沿 Citation → Evidence → PDF 页码回溯。

## 边界和执行流程

```text
浏览器（React SPA，Vite dev server :5173）
  │  /api → Vite proxy → FastAPI :8000（开发期免 CORS）
  ├─ REST：projects / library / project-papers / paper-files / conversations / messages / evidence / index-status / runs / document / elements / file
  └─ SSE：GET /runs/{id}/events/stream（原生 EventSource）
```

- 前端不持有任何业务事实：列表与状态全部来自 PostgreSQL 支撑的 REST API；SSE 事件流由后端从 PostgreSQL 重放/推送（见 `run-event.md`）。
- `/library` 展示 owner 范围的个人文献资产及其 Project 收录范围；Project 页面并行读取当前收录与个人库，可直接收录已有 PaperVersion。移出 Project 只删除 `ProjectPaper`，不会删除 PDF 或解析结果。
- Project 列表使用 `ProjectPaper.selected_version_id` 固定的 Version，不使用“最新 Version”隐式切换语料；该边界会直接被 Phase 2 Retrieval 继承。
- 上传幂等键由浏览器生成：选择新文件时 `crypto.randomUUID()` 生成新 Key，同一文件（同名同大小）重试复用同一 Key（`src/library/uploadIntent.ts`）。
- 上传响应区分新建、复用和已收录：新文件带 `run_id` 进入 Run 页；复用已解析文件时 `run_id=null` 并直接刷新文献库。
- PDF 预览不做自渲染：`<iframe src=".../file#page=N">` 使用浏览器原生 PDF 查看器的页码锚点，零新增依赖。
- RAG 三入口只创建不同 scope 的 Project-scoped Conversation；对话页从 REST 恢复 Message/Claim/Citation，SSE 只驱动进度与缓存失效。
- Project/Paper 归档 UI 使用 `include_archived` 显式查看；「归档个人库资产」不等于「移出项目」，两者在 Project 文献行中是独立动作。

## 关键决定与替代方案

- **原生 EventSource 而非 polyfill/fetch 流**：浏览器自动在重连时携带 `Last-Event-ID`（已收到的最大 sequence），与后端 sequence 游标契约天然对齐。代价：只能 GET、不能自定义 Header（当前 dev-user 认证不需要）。
- **终态主动收束**：后端在 Run 终态后关闭流，但 EventSource 对“正常关闭”也会自动重连。前端收到 `result_committed`、`run_completed`、`run_failed` 或 `run_cancelled` 后主动 `close()`，并以 2s 轮询 `GET /runs/{id}` 兜底。`run_cancel_requested` 是非终态事件，只更新页面并保持 SSE 打开。
- **具名事件逐个订阅**：后端 SSE 帧带 `event: <type>` 字段，EventSource 的 `onmessage` 收不到具名事件，必须按类型 `addEventListener`。类型清单集中在 `src/runs/eventStore.ts` 的 `KNOWN_EVENT_TYPES`，新增事件类型需同步——这是与后端的显式耦合点。
- **事件归并幂等**：重连重放会产生重复 sequence，`applyEvent` 按 sequence 去重并保持升序（`src/runs/eventStore.ts`），与后端"不重不漏"语义对齐。
- **iframe `#page=N` 而非 pdf.js**：满足"Element 定位来源页码"的最小实现；bbox 高亮需要 pdf.js 自渲染，留作后续升级。点击 Element 时以 `key={page}` 重建 iframe，规避原生查看器对同 URL 片段变化不响应的问题。
- **读模型显式返回固定 Version**：个人库与 Project 文献列表都返回非空 `version`；Project 列表中的 Version 来自 `ProjectPaper.selected_version_id`。`GET .../paper-versions/{id}/file` 不要求已有 Parse Revision，但要求当前 Project 确实收录该 Version，越权一律 404。
- **`paper_versions.display_filename` 迁移**：文件名此前只存在于 Run `input_payload`，无法支撑文献库列表展示；作为 Version 的展示字段落库（仅为展示信息，不参与存储路径）。
- **提问幂等意图**：`messageIntent` 在一次问题首次提交时生成 Key，同内容失败重试复用，内容变化才换 Key；成功后清空。它只保存交互意图，不保存服务端 Message/Run 事实。
- **回答与事件解耦**：`answer_committed` 与 Run 终态同事务，前端收到后关闭 EventSource 并失效 messages；回答文本、Claim 与 Citation 始终重新读取 REST，不进入 Event payload。
- **Evidence 阅读路径**：Claim 后的引用标记打开 Evidence 侧栏，显示 excerpt/section/page，再以 `key={page}` 重建 iframe 跳原文；不引入 pdf.js 或 UI 组件库。

## 失败、重试与取消行为

- 上传失败（当前后端的非 PDF/超限均为 400，幂等冲突为 409；代理层也可能返回 413）在表单下方展示 detail；
- 404（越权/不存在）统一展示"资源不存在或无权访问"，不泄漏所有权；
- RAG 稳定业务码转为操作提示：busy 要求稍后重试，not-indexed 引导等待索引，invalid-scope 引导重选范围，归档冲突说明只读或先恢复；
- Run FAILED 时页面展示错误摘要并保留完整事件时间线；
- 取消按钮仅在 `queued`/`running`/`retry_wait` 可用；`cancel_requested` 后按钮消失、轮询直至 `cancelled`；
- SSE 断线由 EventSource 自动重连 + Last-Event-ID 重放，同时每次 error 事件触发一次 Run 查询刷新兜底。

## 测试与运行结果

- Vitest（59 passed，node 环境，纯状态逻辑）：
  - `eventStore`：乱序/重复事件去重排序、终态收束、`run_cancel_requested` 保持连接；
  - `runStatus`：终态表（succeeded/failed/cancelled）与可取消表（queued/running/retry_wait）；
  - `uploadIntent`：同文件复用 Key、改名/改大小生成新 Key；
  - `client`：ApiError 解析与 404/400/409/413 文案映射。
- Phase 2 新增：索引/RAG 具名事件订阅与成功终态、提问 Key 重试复用、稳定业务错误文案、整个 Project/单篇/多篇 scope 选择。
- `npm run build`（tsc strict + vite build）通过。
- 后端非集成回归：`pytest tests -q --ignore=tests/integration`，366 passed、4 skipped（前端改动未改变后端契约）。
- 手动闭环（本地 postgres/valkey + uvicorn + 本地 Worker，真实 Docling 解析）：上传 `text_two_pages.pdf` → SSE 实时事件 → succeeded → papers 列表 `parse_ready=true` → file 端点字节与原件一致（inline disposition）→ SSE `Last-Event-ID: 2` 正确从 sequence 3 重放；queued 状态取消 → cancelled；终态取消 → 409；Playwright 截图验证 4 个页面与 404 呈现。
- 切片 11 Playwright E2E（1 passed）：隔离 PostgreSQL/Valkey Compose + 宿主 API/Worker/Web + 共享临时 Storage；用 Fake Parser 验证创建 Project、新 PDF 异步解析、Run 事件与刷新恢复、Element/PDF 预览、跨 Project 哈希复用、移出只删关系、个人库保留和重新收录。失败时保留 screenshot/video/trace，不维护易碎的像素截图基线。
- Phase 2 Playwright E2E（1 passed；与 Phase 1 合跑 2 passed）：导入后等待 ingestion/indexing、Project 问答与 rag_answer SSE、刷新恢复 Message/Claim/Citation、引用查看 Evidence 与 PDF `#page=N`、单篇 scope、Project 归档只读。E2E 发现并修复 active Project 的归档按钮误调用 restore 的缺陷。

## 代码入口

- 页面：`web/src/pages/`（ProjectsPage、PersonalLibraryPage、LibraryPage、ConversationPage、RunDetailPage、DocumentPage）
- SSE：`web/src/runs/useRunEvents.ts`、`eventStore.ts`、`runStatus.ts`
- RAG 交互状态：`web/src/conversations/messageIntent.ts`、`scopeSelection.ts`
- API 封装：`web/src/api/client.ts`、`types.ts`
- 后端消费端点：`backend/src/literature_agent/api/conversations.py`、`documents.py`、`papers.py`、`projects.py`
- E2E：`web/e2e/phase-01.spec.ts`、`phase-02.spec.ts`、`web/e2e/run.sh`、`web/playwright.config.ts`、`deploy/compose/e2e.yml`

## 已知限制

- Element 仅定位到页码，不做 bbox 高亮；点击切换页码会重建 iframe（整份 PDF 重新加载）。
- Element 列表固定拉取前 200 条，无虚拟滚动/分页 UI。
- 具名 SSE 事件类型清单需与后端手工同步。
- 回答在结构化生成与 Citation Validator 成功后一次性显示，不做 token 级流式输出。
- `index-status` 当前按 Project 文献行独立查询；适合最小 UI 规模，尚无批量状态端点。
- Phase 2 E2E 使用 Fake Provider 固化工程旅程，不代表真实模型回答质量；
- 仅开发代理（Vite proxy），无生产构建部署与 CORS 配置；切片 11 只验证宿主 API/Worker/Web，不宣称完整容器部署。
- E2E 使用 Fake Parser 保持确定性；真实 Docling 由独立 opt-in 契约测试和手动 Smoke 覆盖。

## 60 秒面试说明

这个 UI 同时表达资源模型、可靠执行和引用可信性：三种问答入口都归属 Project，只是 Conversation scope 不同；提问用幂等 Key 创建后台 Run，SSE 按 sequence 重放进度，`answer_committed` 后重新读取数据库中的 Message/Claim/Citation；点击引用标记读取 Evidence 摘录、章节和页码，再用原生 PDF `#page=N` 回到原文。Project/Paper 归档保留历史，并在界面上把个人资产归档与移出 Project 明确分开。
