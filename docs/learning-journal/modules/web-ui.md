# Phase 1–4 Web UI

## 解决的问题

本模块提供 Phase 1–4 的用户可见闭环：管理 Project 与个人文献资产、导入和定位 PDF，从整个
Project、单篇或多篇论文进入带引用的 RAG 对话，并创建、追踪和取消固定 Review Workflow；回答与
Review Claim 均可沿 Citation → Evidence → PDF 页码回溯。Phase 4 切片 3 完成 Review 的 List/
Create/Detail/Stage/Sources 基础旅程，切片 4 已接入结构化 HITL、Matrix、Section/Citation 与
Artifact 下载。

## 边界和执行流程

```text
浏览器（React SPA，Vite dev server :5173）
  │  /api → Vite proxy → FastAPI :8000（开发期免 CORS）
  ├─ REST：projects / library / project-papers / paper-files / conversations / messages / evidence / index-status / runs / document / elements / file
  └─ SSE：GET /runs/{id}/events/stream（原生 EventSource）
```

- Project 工作区用同一语义导航连接 Library、Chat 与 Reviews；现有 Chat 页面和 Conversation 模型
  保持不变，没有为 Review 重建另一套对话产品。
- Review List 读取紧凑的 Project-scoped API；Create 的本地 state 只保存研究问题与幂等意图，成功后
  才清空。列表仅在至少一个 Review 非终态时以 5 秒间隔刷新，空列表和全终态列表关闭轮询。Detail
  并行读取详情和 Sources，`useRunEvents(runId)` 收到业务 Event 后只失效 `review`、`reviews` 与
  `review-sources` Query，真实内容继续从 API 恢复。
- 固定 Stage rail 编码 `review.v1` 的真实顺序，并将持久 `current_stage` 与 Run 状态确定性映射为完成、
  当前、等待和停止。它不实现浏览器状态机，也不根据 Event 推断业务 Stage。
- Outline 表单只把标题、目标、分析维度和 feedback 留在 React 交互状态。提交体携带服务端 Request ID/
  version、Outline Output ID 和 action；相同失败意图复用 `Idempotency-Key`，任一版本或表单语义变化
  才生成新 Key。成功后由 REST 重载，不在本地假设 Workflow 已推进。
- 结构化 edit 还支持 section key、添加、删除和上移/下移。客户端复刻 `outline.v1` 的确定性边界来
  提前解释错误，但后端仍负责最终 Schema、范围、版本和事务校验；Matrix 尚在并行加载时，可选维度
  使用当前 Outline 与 Matrix 的并集，不会暂时清空。存在本地 dirty edit 时不能 approve 旧版本，
  feedback 也明确不携带本地编辑。
- Matrix 与 Section 只渲染版本化 ReviewOutput。Section API 每个 key 只返回最新版本，页面按 Outline
  顺序重排；Evidence ID 点击后再调用现有 Project-scoped Evidence API 获取 PaperVersion 与页码，
  PDF 链接使用受限 file endpoint。Artifact 只使用 Project-scoped content endpoint。

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
- **活动列表才轮询**：Review List 不能只依赖新建成功时的一次失效，否则活动 Run 后续状态会停留在
  旧值；`reviewListRefetchInterval` 复用通用终态表，仅在列表含非终态 Run 时返回 5 秒间隔，不在
  空列表或全终态列表制造持续请求。
- **事件归并幂等**：重连重放会产生重复 sequence，`applyEvent` 按 sequence 去重并保持升序（`src/runs/eventStore.ts`），与后端"不重不漏"语义对齐。
- **iframe `#page=N` 而非 pdf.js**：满足"Element 定位来源页码"的最小实现；bbox 高亮需要 pdf.js 自渲染，留作后续升级。点击 Element 时以 `key={page}` 重建 iframe，规避原生查看器对同 URL 片段变化不响应的问题。
- **读模型显式返回固定 Version**：个人库与 Project 文献列表都返回非空 `version`；Project 列表中的 Version 来自 `ProjectPaper.selected_version_id`。`GET .../paper-versions/{id}/file` 不要求已有 Parse Revision，但要求当前 Project 确实收录该 Version，越权一律 404。
- **`paper_versions.display_filename` 迁移**：文件名此前只存在于 Run `input_payload`，无法支撑文献库列表展示；作为 Version 的展示字段落库（仅为展示信息，不参与存储路径）。
- **提问幂等意图**：`messageIntent` 在一次问题首次提交时生成 Key，同内容失败重试复用，内容变化才换 Key；成功后清空。它只保存交互意图，不保存服务端 Message/Run 事实。
- **回答与事件解耦**：`answer_committed` 与 Run 终态同事务，前端收到后关闭 EventSource 并失效 messages；回答文本、Claim 与 Citation 始终重新读取 REST，不进入 Event payload。
- **Evidence 阅读路径**：Claim 后的引用标记打开 Evidence 侧栏，显示 excerpt/section/page，再以 `key={page}` 重建 iframe 跳原文；不引入 pdf.js 或 UI 组件库。
- **Review 列表是最小读模型**：只返回 `run_id/status/research_question/current_stage/created_at/
  updated_at`；后端用单次 Join 限制 owner、Project 和 Review RunType，避免卡片列表 N+1，也不把
  `config_snapshot`、Prompt 版本或 Checkpoint 暴露给列表。
- **Stage rail 是展示映射**：前端保存固定顺序和中文标签，但合法转换仍只属于后端 Domain；刷新、
  SSE 重连或页面直接打开都以 Detail API 的 `current_stage` 为准。
- **HITL 交互意图不是业务状态**：浏览器可以为同一失败提交保留 Key，但 Request 是否开放、版本是否
  过期、edit 生成哪个批准 Outline，以及 Run/Outbox 是否恢复都由后端原子事务决定；409 后界面提示
  刷新，不在客户端自动改写版本。
- **样式决策**：沿用 Literature Atlas 冷灰纸面、深蓝、朱红、Inter + IBM Plex Mono 和零圆角；唯一
  新签名元素是横向“研究阶段脊柱”，编号与连接线只表达真实 Workflow 顺序。移动端允许 rail 横向
  滚动，键盘 focus、语义 `nav/ol/aria-current` 与 reduced motion 沿用全局约束。

## 失败、重试与取消行为

- 上传失败（当前后端的非 PDF/超限均为 400，幂等冲突为 409；代理层也可能返回 413）在表单下方展示 detail；
- 404（越权/不存在）统一展示"资源不存在或无权访问"，不泄漏所有权；
- RAG 稳定业务码转为操作提示：busy 要求稍后重试，not-indexed 引导等待索引，invalid-scope 引导重选范围，归档冲突说明只读或先恢复；
- Run FAILED 时页面展示错误摘要并保留完整事件时间线；
- 取消按钮仅在 `queued`/`running`/`retry_wait`/`waiting_input`/`waiting_dependency` 可用；
  `cancel_requested` 后按钮消失、轮询直至 `cancelled`；
- SSE 断线由 EventSource 自动重连 + Last-Event-ID 重放，同时每次 error 事件触发一次 Run 查询刷新兜底。
- Review 创建失败保留研究问题与 Idempotency-Key，相同内容重试不会创建第二个 Run；只有成功响应后
  才清空意图并进入 Detail。归档 Project 禁用创建并说明历史只读语义。
- Review 取消按钮只在通用 Run 状态表允许的状态出现，调用 Project-scoped cancel；
  `cancel_requested`、终态和非法转换不展示可重复操作。
- Source 明确区分 discovered、importing、ready 和 failed；部分失败不会把 ready 来源隐藏，稳定
  `failure_code` 可见但 PDF、Prompt 与内部载荷不进入 UI 状态。

## 测试与运行结果

- Vitest（59 passed，node 环境，纯状态逻辑）：
  - `eventStore`：乱序/重复事件去重排序、终态收束、`run_cancel_requested` 保持连接；
  - `runStatus`：终态表（succeeded/failed/cancelled）与可取消表（queued/running/retry_wait）；
  - `uploadIntent`：同文件复用 Key、改名/改大小生成新 Key；
  - `client`：ApiError 解析与 404/400/409/413 文案映射。
- Phase 2 新增：索引/RAG 具名事件订阅与成功终态、提问 Key 重试复用、稳定业务错误文案、整个 Project/单篇/多篇 scope 选择。
- `npm run build`（tsc strict + vite build）通过。
- Phase 4 切片 3：Vitest 全量 `100 passed`，覆盖 Review 创建幂等意图、固定 Stage 顺序/状态映射、
  Source 展示映射、活动列表条件轮询，以及与当前 Review 生产者逐项核对后的具名 SSE 事件订阅/终态
  收束；`npm run build` 通过。Backend 非集成全量
  `615 passed, 4 skipped`，PostgreSQL/Valkey integration 全量 `113 passed`。本切片未启动 dev server，
  没有声称浏览器视觉检查。
- Phase 4 切片 4：Vitest 全量 `118 passed`，新增 HumanInput 同语义重试/版本变化/409 刷新判断、
  Outline 完整结构编辑与 Domain 边界、Matrix/Section/术语结构投影，以及 Project-scoped PDF/
  Artifact content URL；`npm run build` 通过。Backend 非集成全量
  `618 passed, 4 skipped`，PostgreSQL/Valkey integration 全量 `114 passed`，包含 Sections 最新版本、
  类型过滤和 owner/Project 隔离。本切片未启动 dev server，没有声称浏览器视觉/console 检查。
- 后端非集成回归：`pytest tests -q --ignore=tests/integration`，366 passed、4 skipped（前端改动未改变后端契约）。
- 手动闭环（本地 postgres/valkey + uvicorn + 本地 Worker，真实 Docling 解析）：上传 `text_two_pages.pdf` → SSE 实时事件 → succeeded → papers 列表 `parse_ready=true` → file 端点字节与原件一致（inline disposition）→ SSE `Last-Event-ID: 2` 正确从 sequence 3 重放；queued 状态取消 → cancelled；终态取消 → 409；Playwright 截图验证 4 个页面与 404 呈现。
- 切片 11 Playwright E2E（1 passed）：隔离 PostgreSQL/Valkey Compose + 宿主 API/Worker/Web + 共享临时 Storage；用 Fake Parser 验证创建 Project、新 PDF 异步解析、Run 事件与刷新恢复、Element/PDF 预览、跨 Project 哈希复用、移出只删关系、个人库保留和重新收录。失败时保留 screenshot/video/trace，不维护易碎的像素截图基线。
- Phase 2 Playwright E2E（1 passed；与 Phase 1 合跑 2 passed）：导入后等待 ingestion/indexing、Project 问答与 rag_answer SSE、刷新恢复 Message/Claim/Citation、引用查看 Evidence 与 PDF `#page=N`、单篇 scope、Project 归档只读。E2E 发现并修复 active Project 的归档按钮误调用 restore 的缺陷。
- Phase 4 Playwright 新增 2 条 Review 旅程：成功旅程从 UI 创建固定 Review，观察 3 ready + 1 stable
  failed Source，并从持久 Event 验证 `dependency_wait_started`/`dependency_wait_completed`，刷新后从 REST
  恢复首轮 HITL，提交 feedback 后等待 `outline.v2`/Request v2，再次刷新并
  approve，最终读取 Matrix、证据不足、Section/Claim/Citation、Evidence → Project-scoped PDF 页码和
  六类可下载 Artifact；第二条旅程把 UI 取消收敛到可刷新恢复的 `cancelled`。浏览器阻断非 localhost
  请求，并断言无 `pageerror`、无关键 console error。只有当前 Project/Run 的 Outline/Matrix GET 已实际
  返回 404 时，对应精确 `ConsoleMessage.location().url` 的通用浏览器日志才不计为关键错误；旅程主动
  制造另一条本机 404，证明它仍进入错误列表。Phase 1–4 全套最终 `4 passed`。
- E2E harness 现在显式选择 Fake Parser/Embedding/Chat/arXiv、清除 Provider Key 并关闭 Worker Metrics
  端口；不会读取 `.env`。首轮全套暴露 Phase 2 旧测试仍查找已被 Project 工作区导航替代的“返回项目
  文献库”链接，更新为当前 `文献库` 导航契约后单测通过；未修改产品行为。

## 代码入口

- 页面：`web/src/pages/`（ProjectsPage、PersonalLibraryPage、LibraryPage、ConversationPage、RunDetailPage、DocumentPage）
- Review 页面：`web/src/pages/ReviewsPage.tsx`、`ReviewDetailPage.tsx`、
  `web/src/components/ReviewResults.tsx`
- Project 工作区导航：`web/src/components/ProjectNav.tsx`
- Review 纯展示/意图：`web/src/reviews/reviewPresentation.ts`、`reviewIntent.ts`、
  `reviewHumanInput.ts`、`reviewResults.ts`、`reviewListRefresh.ts`
- SSE：`web/src/runs/useRunEvents.ts`、`eventStore.ts`、`runStatus.ts`
- RAG 交互状态：`web/src/conversations/messageIntent.ts`、`scopeSelection.ts`
- API 封装：`web/src/api/client.ts`、`types.ts`
- 后端消费端点：`backend/src/literature_agent/api/conversations.py`、`documents.py`、`papers.py`、`projects.py`
- Review 后端读路径：`backend/src/literature_agent/api/reviews.py`、
  `application/review_query_service.py`、`infrastructure/persistence/review_repository.py`
- E2E：`web/e2e/phase-01.spec.ts`、`phase-02.spec.ts`、`phase-04.spec.ts`、`web/e2e/run.sh`、
  `web/playwright.config.ts`、`deploy/compose/e2e.yml`

## 已知限制

- Element 仅定位到页码，不做 bbox 高亮；点击切换页码会重建 iframe（整份 PDF 重新加载）。
- Element 列表固定拉取前 200 条，无虚拟滚动/分页 UI。
- 具名 SSE 事件类型清单需与后端手工同步。
- 回答在结构化生成与 Citation Validator 成功后一次性显示，不做 token 级流式输出。
- `index-status` 当前按 Project 文献行独立查询；适合最小 UI 规模，尚无批量状态端点。
- Phase 2 E2E 使用 Fake Provider 固化工程旅程，不代表真实模型回答质量；
- Review Detail 已展示真实 Outline HITL、Matrix、Section/Citation 和 Artifact；当前不提供 Matrix/
  Section 在线重写、单节点手工重跑或引用样式切换，这些均不属于 Demo-ready Core v1；
- Playwright 只覆盖少量 Phase 1–4 核心旅程，不穷举全部错误码、并发竞争和跨 owner 路径；这些由
  Domain/Application/API/PostgreSQL/Valkey 矩阵承担。
- 仅开发代理（Vite proxy），无生产构建部署与 CORS 配置；E2E 验证宿主 API/Worker/Web，不宣称完整
  容器部署。
- E2E 使用全套 Fake Adapter 保持确定性；真实 Docling、arXiv 和 Provider 由独立 opt-in Smoke/报告
  覆盖，不把 Fake 浏览器旅程包装成真实生成质量。

## 60 秒面试说明

这个 UI 同时表达资源模型、可靠执行和引用可信性：Project 工作区统一连接文献、Chat 和 Review；
问答与 Review 创建都用可重试的幂等意图，SSE 只提示 TanStack Query 重新读取 PostgreSQL 事实。
Review Detail 不在浏览器复制状态机，而是把固定 `review.v1` 顺序与服务端 `current_stage` 映射成研究
阶段脊柱，并展示 Step、部分失败 Source、等待和取消。RAG 仍从 Claim/Citation 回到 Evidence 与 PDF
页码；Project/Paper 归档保留历史，并把个人资产归档、移出 Project 与禁止新 Workflow 明确区分。
