# Phase 1–6 Web UI

## 解决的问题

本模块提供 Phase 1–4 的用户可见闭环：管理 Project 与个人文献资产、导入和定位 PDF，从整个
Project、单篇或多篇论文进入带引用的 RAG 对话，并创建、追踪和取消固定 Review Workflow；回答与
Review Claim 均可沿 Citation → Evidence → PDF 页码回溯。Phase 4 切片 3 完成 Review 的 List/
Create/Detail/Stage/Sources 基础旅程，切片 4 已接入结构化 HITL、Matrix、Section/Citation 与
Artifact 下载。
Phase 5 切片 8 在同一 Project 工作区加入持续 Research Agent：用户可创建 Session，在首轮前选择
Evidence Matrix 和版本固定的能力 Profile，并通过逐消息 Turn 连续研究；刷新后由 REST 恢复业务消息、
引用和候选成果，由同一通用 Run/SSE 基础设施恢复执行进度。

## 边界和执行流程

```text
浏览器（React SPA，Vite dev server :5173）
  │  /api → Vite proxy → FastAPI :8000（开发期免 CORS）
  ├─ REST：projects / library / project-papers / paper-files / conversations / messages / evidence / index-status / runs / document / elements / file
  └─ SSE：GET /runs/{id}/events/stream（原生 EventSource）
```

- Project 是统一工作空间，文献库是三种研究模式共享的资源底座。全站复用固定 `AppSidebar` 与 56px
  `PageBar`；项目四入口只出现在 Sidebar 的当前 Project 分区，不再由页面复制 Header、Mode Nav 或 Hero。
- `AppSidebar` 桌面默认宽 232px，展开时由右缘 separator 在 216–288px 内调整，支持 pointer、方向键、
  Home/End 和双击复位；窄屏仍固定为 56px icon rail。展开宽度与折叠状态共享 v1 `localStorage` 偏好，
  既有只含 `collapsed` 的记录自动回退默认宽度。导航、会话树、PageBar 次级文字和 Chat 创建页提示统一
  提升字号、字重与 muted 对比度，不改变页面信息架构。
- Project 工作区现在用“文献库 / 文献问答 / 综述 / 研究助手”区分三种产品模式。Research Agent 使用
  独立 `AgentSession/AgentTurnRun`，不会把 RAG Conversation 冒充为持续 Agent Thread，也不复制官方
  Deep Agents UI 的数据层。
- Research Agent 无 Session 时复用 Chat/Review 的“功能标题—中央选择—底部输入”工作台骨架。中央三张
  方向卡只把研究缺口、方法比较或证据核查写入本地会话标题草稿；底部 Composer 才是唯一创建入口，
  提交前不会创建 Session 或运行 Agent。项目索引就绪数在 Composer 中说明可用上下文，Evidence Matrix、
  附件和研究能力继续留在进入 Session 后配置，避免入口页提前堆叠高级设置；归档 Project 禁止创建。
- Review List 读取紧凑的 Project-scoped API；Create 的本地 state 只保存研究问题与幂等意图，成功后
  才清空。列表仅在至少一个 Review 非终态时以 5 秒间隔刷新，空列表和全终态列表关闭轮询。Detail
  并行读取详情和 Sources，`useRunEvents(runId)` 收到业务 Event 后只失效 `review`、`reviews` 与
  `review-sources` Query，真实内容继续从 API 恢复。
- 固定 Stage rail 编码 `review.v1` 的真实顺序，并将持久 `current_stage` 与 Run 状态确定性映射为完成、
  当前、等待和停止。详情页首屏只把它投影为“准备来源 / 整理证据 / 撰写综述 / 校验与导出”四个产品
  阶段；完整 14 步、步骤记录、Workflow 版本和最近事件统一收进底部“执行信息”。完成态不再显示大面积
  进度，而是直接展示来源数、Matrix 行数、引用校验状态和结果导航。两层展示都不实现浏览器状态机，
  也不根据 Event 推断业务 Stage。
- Outline 表单只把标题、目标、分析维度和 feedback 留在 React 交互状态。提交体携带服务端 Request ID/
  version、Outline Output ID 和 action；相同失败意图复用 `Idempotency-Key`，任一版本或表单语义变化
  才生成新 Key。成功后由 REST 重载，不在本地假设 Workflow 已推进。
- 结构化 edit 还支持 section key、添加、删除和上移/下移。客户端复刻 `outline.v1` 的确定性边界来
  提前解释错误，但后端仍负责最终 Schema、范围、版本和事务校验；Matrix 尚在并行加载时，可选维度
  使用当前 Outline 与 Matrix 的并集，不会暂时清空。存在本地 dirty edit 时不能 approve 旧版本，
  feedback 也明确不携带本地编辑。
- Matrix 与 Section 只渲染版本化 ReviewOutput。Section API 每个 key 只返回最新版本，页面按 Outline
  顺序重排；Evidence ID 点击后再调用现有 Project-scoped Evidence API 获取 PaperVersion 与页码，
  PDF 链接使用受限 file endpoint。Matrix 以 Review Source 快照把 `paper_id` 投影为论文标题，短 ID
  降为次级信息；常见 dimension key 转换为中文标签，Evidence ID 在当前结果中稳定编号为 `[1]`、
  `[2]`，点击后才读取详情。Artifact 只使用 Project-scoped content endpoint。
- Agent 页并行读取 Project、Session、消息、Review Matrix、MCP/Skill Catalog 与 Profile；TanStack
  Query 持有服务端状态，本地只保存输入意图、能力配置草稿和当前选中的 Evidence。每条消息使用稳定
  `Idempotency-Key`，活动 Turn 时禁发；SSE 只显示允许列表中的业务活动，终态后失效 Session、Message
  和 Turn Query，正文、Claim、Citation 和 candidate 始终从 REST 读取。
- 桌面端是 Session rail / Conversation / Turn Inspector 三栏，页面 chrome 固定在 viewport 内，三栏
  独立滚动；中栏消息与研究活动内部滚动，composer 固定在底部。左右分隔条支持指针拖动、方向键与双击
  复位，宽度按 v1 schema 保存到 `localStorage`。能力配置、Evidence Matrix 与附件位于 composer 内；
  details 展开只压缩 timeline，不遮挡 composer。
- Turn Inspector 固定为“证据 / 浏览器 / 成果”三个可访问 tab。证据区显示不可变索引快照、Matrix 与
  Claim/Citation/Evidence；浏览器区复用 noVNC；成果区组合正式 Artifact、内部 Candidate 与 Manifest。
  inactive panel 退出布局但保持 Browser 组件挂载。前端不解析回答正文制造引用，也不读取 candidate 内容。
- Agent 消息按 `turn_run_id` 分组，每一轮都通过 REST 恢复自己的筛选 Event、脱敏 ToolExecution 与
  Usage/Budget；只有当前候选 Turn 额外订阅 SSE，并按 sequence 覆盖 REST 快照。研究过程和每次工具调用
  内联在对应消息组中，可分别折叠；工具展开后显示服务端返回的有界脱敏输入/输出预览。用户消息靠右，
  助手正文采用无角色标题的左侧自然流，运行中只使用低干扰旋转/呼吸动画且支持 reduced-motion。
  Manifest 只展示公开来源状态、hash、大小和服务端实际返回的 URL。无 Session 时附件查询保持 disabled，
  不再请求空 session。

- 前端不持有任何业务事实：列表与状态全部来自 PostgreSQL 支撑的 REST API；SSE 事件流由后端从 PostgreSQL 重放/推送（见 `run-event.md`）。
- `/library` 展示 owner 范围的个人文献资产及其 Project 收录范围；Project 页面并行读取当前收录与个人库，可直接收录已有 PaperVersion。移出 Project 只删除 `ProjectPaper`，不会删除 PDF 或解析结果。
- 个人文献库使用约 68px 的紧凑书目行：标题为一级信息，文件名、大小和添加日期为二级信息；状态脊柱
  表达已解析、处理中和归档。工具栏支持标题/文件名搜索、状态与 Project 筛选、最近添加/标题排序；
  Project 标签最多直接显示两个，其余以 `+N` 汇总。
- `PaperTitle` 是个人库、Project 文献行、已有文献复用列表和 Chat 范围选择的共享投影：优先显示
  `Paper.title`，缺失时回退 `PaperVersion.display_filename`。标题只通过 CSS 截断，完整文本保留在
  `title` 属性中供鼠标悬停查看；文件名仍作为可追溯的次级信息显示。
- Project 列表使用 `ProjectPaper.selected_version_id` 固定的 Version，不使用“最新 Version”隐式切换语料；该边界会直接被 Phase 2 Retrieval 继承。
- 上传幂等键由浏览器生成：选择新文件时 `crypto.randomUUID()` 生成新 Key，同一文件（同名同大小）重试复用同一 Key（`src/library/uploadIntent.ts`）。
- 上传响应区分新建、复用和已收录：新文件带 `run_id` 进入 Run 页；复用已解析文件时 `run_id=null` 并直接刷新文献库。
- PDF 预览不做自渲染：`<iframe src=".../file#page=N">` 使用浏览器原生 PDF 查看器的页码锚点，零新增依赖。
- RAG 创建和历史位于 canonical `/projects/:projectId/chat`；Library 的整个 Project、单篇和多篇入口只
  导航并带入范围，不再直接创建 Conversation。`paper_id` 预选仅接受当前 Project Paper API 返回的 ID。
  详情内部链接使用 `/chat/:conversationId`，旧 `/conversations/:conversationId` 保留兼容；对话页从
  REST 恢复 Message/Claim/Citation，SSE 只驱动进度与缓存失效。
- Chat 首页提供方法、实验和结论边界三个范围中立的固定问题模板，并以工作区底部唯一 Composer 承载
  推荐或自由问题草稿。模板按钮只选择草稿并打开中央范围 Dialog，不创建 Conversation、不提交模型；
  自由问题点击“创建问答”后进入同一 Dialog，取消只丢弃范围临时修改。确认后固定模板通过受控
  `question_template` ID、自定义问题通过一次性 route state 交给目标 Composer，初始化后立即消费，
  Composer 只预填并等待用户明确发送；主操作不再创建空白 Conversation。
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
- **推荐问题不是隐式 Run**：固定问题模板属于版本内前端内容，不从模型动态生成；点击只改变本地草稿并
  打开范围 Dialog。Dialog 的范围选择是可取消的本地临时状态，只有最终确认才创建 Conversation。受控
  模板 ID 与一次性 route state 只绑定刚创建的目标 Conversation，详情页消费后从 history entry 清除。
  这样保留用户发送前确认，也避免前端串联“创建 Conversation + 提交首条 Message”产生部分成功。
- **回答与事件解耦**：`answer_committed` 与 Run 终态同事务，前端收到后关闭 EventSource 并失效 messages；回答文本、Claim 与 Citation 始终重新读取 REST，不进入 Event payload。
- **Evidence 阅读路径**：Claim 后的引用标记打开 Evidence 侧栏，显示 excerpt/section/page，再以 `key={page}` 重建 iframe 跳原文；不引入 pdf.js 或 UI 组件库。
- **Review 列表是最小读模型**：除 Run/问题/阶段外，只附加 canonical aggregate Evidence Matrix 的
  `output_id/version/row_count/valid_papers/failed_papers` 摘要；后端用 owner/Project-scoped 批量查询，
  不逐 Review probing，也不把 `config_snapshot`、Prompt 版本或 Checkpoint 暴露给列表。
- **Stage rail 是展示映射**：前端保存固定顺序和中文标签，但合法转换仍只属于后端 Domain；刷新、
  SSE 重连或页面直接打开都以 Detail API 的 `current_stage` 为准。四阶段产品进度只是固定 14 步的
  分组投影，不改变 Workflow、Checkpoint、取消或重试语义；完整流程仍可从执行信息展开核对。
- **HITL 交互意图不是业务状态**：浏览器可以为同一失败提交保留 Key，但 Request 是否开放、版本是否
  过期、edit 生成哪个批准 Outline，以及 Run/Outbox 是否恢复都由后端原子事务决定；409 后界面提示
  刷新，不在客户端自动改写版本。
- **样式决策**：沿用 Literature Atlas 的纯白纸面、墨蓝/蓝色、朱红、Inter + IBM Plex Mono 和零圆角；
  冷灰画布网格降为 64px 极低对比度，主要卡片使用弱边框/弱阴影，仅 Project 创建引导区保留大面积深色。
  Workflow 阶段脊柱继续只表达真实顺序；全局 skip link、focus-visible、touch-action 和 reduced motion
  构成最低可访问性约束，不把桌面走查宣称为全量 WCAG 认证。
- **Agent UI 不接 SDK 数据层**：产品 Session、Message、Run、Evidence 和 candidate 都来自平台 API；
  Deep Agents Thread/Checkpoint/内部文件不进入浏览器。这样保留原生 Agent 上下文管理，同时不绕过
  owner/Project 权限、业务恢复和审计事实。
- **研究助手入口只创建持续上下文**：方向模板用于降低空白页启动成本，但选择模板只修改标题草稿，
  不等同于发送研究问题或执行 Turn。会话创建与首轮能力/证据配置保持两个明确动作，既沿用全站统一的
  工作台结构，也保留 Agent Session 与一次性 Chat/Review Run 的产品差异。
- **能力配置是安全的产品语言**：界面只展示平台维护的能力名称、声明参数和研究方法，不暴露 MCP
  endpoint/transport/command/env、SDK Thread、Sandbox 或 Secret。Skill 在第一条产品消息后只读；MCP
  可按既有 Profile/Policy 契约调整，未保存草稿会阻止发送。
- **Evidence Matrix 通过 Output ID 固化**：可用性只取决于当前 owner/Project 下是否存在 canonical
  aggregate `evidence-matrix` output，不取决于父 Review 最终状态；因此后续 Section 失败但已产出合法
  Matrix 的 Review 仍可选择，失败且无 Matrix 的 Review 会被排除。提交 Turn 继续发送并由服务端校验
  `output_id`；正常后续轮可沿用上一轮冻结值，不让浏览器猜测“最新 Matrix”。
- **Project chrome 统一但业务模型不合并**：共享 AppSidebar/PageBar 与三栏 resize 只解决入口位置、viewport 和
  可访问交互；RAG `Conversation`、Review Run 与 AgentSession/Turn 仍使用各自 API 和恢复语义。
- **栏宽偏好隔离**：RAG 与 Agent 使用同一个纯 resize helper 和可聚焦 separator，各自保存
  `literature-agent:chat-workspace` / `agent-workspace` v1 最小记录；非法/旧版本回退默认值，Conversation、
  Evidence、Session 和 SDK 状态不进入 localStorage。
- **应用侧栏只做小范围 UI 调整**：默认宽度保持 232px，216px 下限保留主导航与操作空间，288px 上限
  避免挤占研究工作区。separator 常态只显示低对比 grip，hover/focus/drag 时增强；拖动期间禁用文本选择。
  宽度与折叠状态沿用同一个版本化 UI 记录，不新建服务端偏好或把 Project/Session 事实写入浏览器。
- **Review 入口只承担创建**：Review List 不再在创建页下方复制一套重型记录区；已有 Review 与
  Conversation、AgentSession 一样进入侧栏可折叠子树，创建页只保留研究问题、项目论文范围和自动补充
  策略。创建页沿用 Chat 首页的“功能说明—中央范围选择—底部 Composer”空间结构，中间只负责选择
  Project 论文和自动补充策略，底部只负责输入研究问题与启动任务；两种功能共享工作台节奏，但不合并
  Conversation 与 Review 的业务交互。侧栏仍读取 Project-scoped 最小 Review List，并仅在存在活动
  任务时轮询。

## 失败、重试与取消行为

- 上传失败（当前后端的非 PDF/超限均为 400，幂等冲突为 409；代理层也可能返回 413）在表单下方展示 detail；
- 404（越权/不存在）统一展示"资源不存在或无权访问"，不泄漏所有权；
- RAG 稳定业务码转为操作提示：busy 要求稍后重试，not-indexed 引导等待索引，invalid-scope 引导重选范围，归档冲突说明只读或先恢复；
- Run FAILED 时页面展示错误摘要并保留完整事件时间线；
- 取消按钮仅在 `queued`/`running`/`retry_wait`/`waiting_input`/`waiting_dependency` 可用；
  `cancel_requested` 后按钮消失、轮询直至 `cancelled`；
- SSE 断线由 EventSource 自动重连 + Last-Event-ID 重放，同时每次 error 事件触发一次 Run 查询刷新兜底。
- Agent Turn 复用相同恢复机制：刷新先读取 Session 当前活动指针或最后一条 Message 的 `turn_run_id`，
  再由 `GET /runs/{id}` 与具名 SSE 收束；取消调用通用 Run cancel。UI 只展示筛选后的阶段标签，不保存
  Event payload、模型思考、Prompt 或 Tool 原始输出。
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
- Phase 5 切片 8：主审修正后 Vitest 全量 `131 passed`，新增稳定 Agent 消息意图、Session identity/
  Project 闭包、发送/Skill 锁定/Project Index/
  candidate 展示规则和 Agent Run 具名事件/终态；`npm run build` 通过。Playwright 新增 1 条完全离线
  Agent 旅程，完成创建 Session、首轮前 Skill 配置、Matrix 选择、第一轮、刷新、第二轮、Evidence
  Margin 与 staged candidate 展示，最终 `1 passed`；旅程阻断非 localhost 请求并断言无 page error。
  后端相关 API/Application/Executor/PostgreSQL Repository 为 `30 passed`，两轮/可靠性集成为
  `4 passed`，非集成全量回归为 `952 passed, 5 skipped`。全量 `ruff check .` 仍报告 50 个既有
  Alembic migration 格式问题；本切片覆盖的
  `src`/测试定向 Ruff 通过，全量 Pyright 为 0 errors。
- 主审补强后，同 Project 跨 Turn Citation 在修复前真实红灯、增加 Evidence run 闭包后相关后端
  定向 `15 passed`、两轮集成 `1 passed`；前端 build 与 Phase 5 E2E `1 passed (36.2s)`。切换
  Project/Session 会重建本地交互状态；Session/路由 Project 不匹配时停止子查询和渲染；能力部分成功
  后等待所有请求收束并重新读取两个 Profile，失败草稿仍保留。
- Matrix/viewport 补强先取得 4 条后端行为红灯与 3 个前端模块红灯；实现后后端 Review Matrix 批量
  查询、失败 Review 可选、无 Matrix 排除和单查询 Project ready index 的定向回归为 `28 passed`，
  前端全量为 `131 passed`，production build、定向 Ruff 与 Pyright 均通过。能力浮层首次 E2E 暴露
  保存后仍拦截 composer 点击，关闭浮层后 Phase 5 完整离线旅程为 `1 passed (36.8s)`。
- 随后的真实数据验收发现同一 Review 的 Section、per-paper Matrix 与 aggregate Matrix 可共享 version；
  Repository 外层连接补齐 canonical type/key 限制，碰撞用例先返回 3 项、修复后只返回 aggregate Matrix，
  相关 Repository/Application/API 回归为 `11 passed`。
- 1440×1000 有头验收确认 viewport 与三栏滚动正确，但初版 composer 为 324px、消息区仅 213px；将
  Matrix 改成紧凑横向 context row、textarea 调整为默认 80px、消息 label 视觉隐藏但保留关联后，build、
  `131 passed` 与 Phase 5 E2E `1 passed (36.4s)` 通过，最终像素高度由主智能体有头复验。
- 主智能体最终独立合并回归为后端 `31 passed in 80.51s`、前端 `128 passed`、定向 Ruff 通过、Pyright
  0 errors 与 production build 通过；Phase 4 取消 E2E 复测 `1 passed (10.7s)`。有头 `playwright-cli`
  在 1440×1000 下确认三栏计算宽度为 `220px / 586px / 350px` 且无横向溢出，空态、能力 Catalog、
  Session rail 与 Evidence Margin 均可访问。唯一 Console error 是既有 `/favicon.ico` 404；业务 API
  请求均为本地 200，本切片未顺便修改站点资产。
- Phase 5 切片 8.1：canonical route、Project-scoped URL 预选、Conversation Project 闭包与 versioned
  layout 的首轮 Vitest 因模块缺失真实红灯；主审补强后，Conversation 与 Agent Session 在 Project
  闭包查询完成前均禁止读取子资源和提交消息，相关纯规则测试先红后绿；前端全量
  `19 files / 140 tests`，production build
  通过。Phase 2 离线 E2E `1 passed (16.3s)`，覆盖 canonical Chat、Project/单篇 scope、刷新恢复、
  Evidence/PDF 与归档只读；Phase 5 Agent 回归 `1 passed (36.2s)`。普通测试未访问真实模型、网络、MCP
  或 Sandbox。
- Phase 6 Slice 8.3：Inspector/Tool/Manifest/空 Session 查询的 TDD 先得到 4 个缺失模块失败，完成后
  定向为 4 files / 5 passed；完整 Vitest 为 29 files / 169 passed，production build 通过。Phase 5
  离线旅程在适配“成果/证据”tab 的可访问操作后保持原业务断言，最终 `1 passed (37.2s)`；新 Turn 的
  ToolExecution/Manifest 查询均返回 200。
- 1440×1000 有头走查确认 Chat/Agent 的 document `scrollHeight` 等于 1000px viewport，timeline 与
  Inspector 独立滚动且 composer 可见；Inspector 从“证据”按 ArrowRight 聚焦“浏览器”，能力 details
  展开后 document 仍不滚动。noVNC 继续输出为独立 `rfb` lazy chunk。历史数据走查中一个早于 Usage
  事实落地的 Turn 对 ToolExecution 查询返回 404，UI 只显示安全错误，未在纯前端伪造兼容数据。
- Phase 6 Slice 8.4：AppFrame 与 Artifact 图片尺寸的 TDD 先得到 2 个失败，完成后定向为 2 files /
  3 passed；完整 Vitest 为 30 files / 170 passed，production build 与 `git diff --check` 通过。
- 1440×1000 走查覆盖首页、Project 文献库、Chat、Reviews 和 Agent：主要工作区均为白色纸面，
  Review/RAG/Agent 强调区统一为朱红状态线；Chat/Agent document 高度等于 viewport、三栏无横向溢出、
  composer 可见。Tab 可显示 skip link，Enter 后焦点进入 `main-content`。控制台仍记录既有 favicon 404，
  以及所选历史 Turn 早于 Usage 事实落地导致的 ToolExecution 404；均未在视觉切片中掩盖。
- Phase 6 Slice 8.5：生产 Browser panel/noVNC、短时 ticket、Vite WebSocket proxy 与有界 VNC bridge
  完成同 Sandbox 真实回路；向 Chromium 输入 marker 后，由保持打开的同 generation Playwright MCP
  session 回读，显式 Smoke 为 1 passed（15.96s）。最终前端仍为 30 files / 170 passed，production
  build 通过，Phase 5 Agent UI 离线旅程为 1 passed（36.6s）。该组合证据不冒充真实模型的完整下一
  Turn E2E、跨浏览器认证或公网多用户 Browser 安全认证。
- 2026-08-31 的 Chat 入口增强先以固定模板、草稿交接和缺失范围 Dialog 得到真实红灯，再补齐可访问问题
  按钮、工作区底部唯一 Composer、中央范围确认与取消回滚。定向为 5 files / 13 passed，完整 Vitest 为
  40 files / 195 passed，production build 通过。Phase 2 Fake 浏览器闭环为 1 passed（18.4s），覆盖推荐
  问题打开 Dialog、问题保留、范围草稿取消、焦点恢复、自由问题、单篇预选、新 Conversation 只预填不
  自动发送，以及既有 RAG/刷新/Citation/PDF/归档只读旅程；测试页未捕获 JavaScript page error，未调用
  真实模型或外部网络。1280×720 临时截图走查确认研究切入点与底部 Composer 同屏、Dialog 内部滚动区和
  固定操作区清晰；走查后又将共享遮罩收敛到 10% 冷墨色与 1px 模糊，临时截图未作为像素基线保留。
- 2026-08-31 的侧栏可读性与宽度增强先以偏好 schema、宽度边界和 separator 语义测试得到真实红灯；
  完成后前端全量为 `40 files / 197 passed`，production build 通过。隔离 Playwright 侧栏用例
  `1 passed (1.9s)`，覆盖方向键、Home/End、216–288px 边界、pointer 拖动、刷新恢复、折叠恢复、双击
  复位与 880px 窄屏 56px icon rail；Phase 2 Fake 问答闭环另为 `1 passed (7.2s)`。1280×720 临时截图
  确认默认宽度下项目名、导航、会话树、问题卡片和 Composer 无挤压；截图未作为像素基线保留。
- 2026-08-31 的 Review 入口收敛移除双栏流程说明与页面内任务列表，把历史任务合并到“文献研究”侧栏
  子树。前端全量为 `40 files / 198 passed`，production build 通过；1600×1000 本地只读走查确认研究
  问题、项目论文、自动补充和提交动作按单一路径排列，侧栏当前任务可直接进入。控制台仅保留既有
  `favicon.ico` 404，未创建 Review 或调用外部网络。

## 代码入口

- 应用壳与可访问入口：`web/src/App.tsx`、`web/src/App.test.tsx`
- 页面：`web/src/pages/`（ProjectsPage、PersonalLibraryPage、LibraryPage、ChatPage、ConversationPage、RunDetailPage、DocumentPage）；Chat 范围弹窗为 `web/src/components/ChatScopeDialog.tsx`
- Review 页面：`web/src/pages/ReviewsPage.tsx`、`ReviewDetailPage.tsx`、
  `web/src/components/ReviewResults.tsx`
- Agent 页面：`web/src/pages/AgentPage.tsx`、`web/src/components/AgentSessionRail.tsx`、
  `AgentCapabilityPanel.tsx`、`AgentInspector.tsx`、`AgentEvidenceMargin.tsx`、`AgentResearchActivity.tsx`、
  `AgentTurnOutputs.tsx`、`AgentManifestList.tsx`；纯交互规则位于 `web/src/agent/`。
- Project 工作区：`web/src/components/AppSidebar.tsx`、`PageBar.tsx`、`ChatWorkspaceFrame.tsx`、
  `ConversationRail.tsx`、`WorkspaceResizeSeparator.tsx`；canonical 路由、
  Project-scoped 预选与 versioned layout 纯规则位于 `web/src/workspace/`。
- Chat 问题入口：`web/src/components/QuestionStarterList.tsx`、
  `web/src/conversations/questionTemplates.ts`、`web/src/pages/ChatPage.tsx` 与 `ConversationPage.tsx`。
- Review 纯展示/意图：`web/src/reviews/reviewPresentation.ts`、`reviewIntent.ts`、
  `reviewHumanInput.ts`、`reviewResults.ts`、`reviewListRefresh.ts`
- SSE：`web/src/runs/useRunEvents.ts`、`eventStore.ts`、`runStatus.ts`
- RAG 交互状态：`web/src/conversations/messageIntent.ts`、`scopeSelection.ts`
- 文献标题与目录筛选：`web/src/components/PaperTitle.tsx`、`web/src/library/paperCatalog.ts`
- API 封装：`web/src/api/client.ts`、`types.ts`
- 后端消费端点：`backend/src/literature_agent/api/conversations.py`、`documents.py`、`papers.py`、`projects.py`
- Review 后端读路径：`backend/src/literature_agent/api/reviews.py`、
  `application/review_query_service.py`、`infrastructure/persistence/review_repository.py`
- E2E：`web/e2e/phase-01.spec.ts`、`phase-02.spec.ts`、`phase-04.spec.ts`、`phase-05.spec.ts`、`web/e2e/run.sh`、
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
- Agent UI 已提供 Browser/noVNC、输入附件、正式 Artifact 与 Manifest 展示，但仍不提供通用 Workspace
  文件管理、fork/rewind、candidate 内容查看或移动端 Drawer；窄屏只避免阻断性溢出。Fake Runtime 固定回答没有 Citation 且完成
  很快，因此 E2E 不把非空 Agent Citation 或运行中取消作为稳定断言；相应业务边界由后端和通用 Run
  分层测试覆盖。
- UI 中的“研究过程”来自白名单 Run Event，不是模型隐藏思维链；模型 Prompt、原始 chain-of-thought 和
  未脱敏 Tool payload 均不进入前端。迁移前 Tool 调用没有可恢复预览时明确显示缺失说明。
- Chat/Agent 的 viewport 三栏以桌面为验收主体；窄屏顺序展开且不建设 Drawer。栏宽偏好只在各自模式
  内复用，不跨设备同步。
- 应用侧栏宽度同样只保存在当前浏览器，不跨设备同步；窄于 900px 时忽略展开宽度并固定显示 56px
  icon rail，不在移动端提供拖拽。
- 本轮只完成键盘入口、焦点、动效降级与桌面页面走查；未执行自动色彩对比审计、全量 WCAG、跨浏览器
  或移动端认证。既有 favicon 404 与历史 Agent Turn ToolExecution 404 仍按其真实来源保留。
- 2026-08-30 的历史 Turn/工具展开回归使用真实本地会话验证：页面恢复 4 个 Turn、9 个 Tool disclosure、
  4 个研究过程 disclosure，角色标题计数为 0，浏览器控制台无错误；前端全量为 32 files / 176 passed，
  production build 通过。
- 2026-08-30 的文献标题与个人库密度优化增加 2 个测试文件 / 6 个断言，覆盖标题回退、完整 title
  属性、组合筛选和稳定排序；前端全量为 36 files / 187 passed，生产构建通过。后端标题领域/API/导入
  定向回归为 43 passed，迁移 head 为
  `a9e3d5f7b1c4`，PostgreSQL 往返与升降级为 6 passed。1440×1000 与 390×844 浏览器走查确认书目行
  为 68px、桌面单行/窄屏双行截断、搜索生效且均无横向溢出。
- 2026-08-31 的 Review 创建页布局收敛后，前端全量为 40 files / 198 passed，production build 通过。
  1600×1000 浏览器走查确认来源卡位于中央工作区、研究问题 Composer 固定在工作区底部，页面无横向
  溢出；控制台仅保留既有 `favicon.ico` 404。
- 2026-08-31 的 Review Detail 产品化调整新增四阶段投影和维度标签测试，前端全量为 40 files /
  202 passed，production build 通过；真实完成任务与取消中任务的浏览器走查确认结果优先、四阶段进度、
  论文标题、中文维度、编号引用和默认收起的执行信息均正确，两种状态无横向溢出或 Vite 错误浮层。
- 2026-08-31 的 Research Agent 创建入口统一为中央研究方向与底部 Composer；前端全量为 40 files /
  202 passed，production build 与 `git diff --check` 通过。1280×720 本地只读走查确认三张方向卡、
  14 篇就绪索引提示和创建操作同屏，中央区域没有独立滚动，卡片与 Composer 间距约 34px，页面无横向
  溢出或 Vite 错误浮层；走查未创建 Session、未发送 Turn。
- 2026-08-31 为 `runtime_output_invalid` 增加稳定的“回答引用格式未通过校验”说明，明确模型虽完成研究但
  回复未写入会话，并引导重新发起；错误投影不回显原始模型输出、Provider 信息或内部 checkpoint。
  平台 Skill 新版本发布后，能力面板对未选择会话只显示最新版；已有选择继续显示其精确旧版本，使锁定
  Session 可恢复且不会出现两个同名研究方法。前端全量为 40 files / 204 passed，production build 通过。

## 60 秒面试说明

这个 UI 同时表达资源模型、可靠执行和引用可信性：Project 是工作空间，文献库提供资源底座，canonical
Chat、Review 和 Research Agent 是三种平级研究模式；
问答与 Review 创建都用可重试的幂等意图，SSE 只提示 TanStack Query 重新读取 PostgreSQL 事实。
Review Detail 不在浏览器复制状态机，而是把固定 `review.v1` 顺序与服务端 `current_stage` 映射成研究
阶段脊柱，并展示 Step、部分失败 Source、等待和取消。RAG 仍从 Claim/Citation 回到 Evidence 与 PDF
页码；Agent 把每条用户消息建模为独立 Turn，却复用同一 Session/SDK Thread，并通过 REST 恢复产品
消息、能力快照、Evidence Margin 和候选成果。浏览器不接触 SDK Checkpoint、Secret 或原始 Tool 输出，
也不从正文猜引用；Project/Paper 归档保留历史，并把个人资产归档、移出 Project 与禁止新 Workflow
明确区分。
