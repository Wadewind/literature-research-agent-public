# Phase 3：可暂停恢复的固定文献综述 Workflow

## 状态

- 当前状态：开发中（切片 1 已完成，等待确认）
- 需求讨论：2026-08-22 已完成第一轮收敛
- 关联决策：[ADR-0003：Phase 3 固定文献综述 Workflow](../decisions/0003-phase-3-fixed-review-workflow.md)

## 1. 阶段目标

本阶段在 Phase 2 已完成的文献导入、索引、检索、Evidence 和引用校验能力之上，实现一个固定、可持久化、可暂停恢复的文献综述 Workflow。

用户输入研究问题后，系统自动从 arXiv 检索并导入论文，等待论文完成解析与索引，提取跨论文 Evidence Matrix，生成综述大纲并暂停等待用户确认。用户可以批准、编辑或反馈大纲；批准后系统按章节生成带数字引用的 Markdown 综述并导出 Artifact。

```text
研究问题
  → 生成检索策略与分析维度
  → arXiv 检索并自动导入前 N 篇论文
  → 等待各论文完成 Ingestion/Indexing
  → 为每篇论文提取 Evidence Matrix
  → 生成并持久化大纲
  → LangGraph interrupt：人工确认/编辑/反馈大纲
  → 分章节写作与引用校验
  → 全文一致性检查
  → 导出 Markdown Artifact
```

本阶段的学习重点不是构建完整论文筛选产品，而是掌握 LangGraph 的 checkpoint、interrupt、resume，以及它们和业务 Run、Attempt、Event、Outbox、子 Run 依赖之间的边界。

## 2. 前置条件

- Phase 1 的 Project、Paper、PaperVersion、可靠异步导入、Run/Event/Outbox 已完成；
- Phase 2 的 ChunkSet、Embedding、混合检索、Evidence、Citation Validator 已完成；
- PostgreSQL 是业务状态、Event、Evidence 和 Artifact 的事实来源；
- Valkey/ARQ 只承担 Job 投递和实时通知，不承担 Workflow 事实状态；
- 普通自动测试使用 Fake Model、Fake Embedding 和 HTTP Mock，不调用真实 arXiv 或付费模型。

## 3. 已确认的范围决策

### 3.1 固定 Workflow，不做通用编排器

第一版只支持上述固定节点和固定跳转，不实现 Workflow Canvas、用户自定义节点、多 Agent 或动态工具规划。

### 3.2 只使用 arXiv，并自动纳入结果

- 论文发现、元数据和 PDF 下载统一使用 arXiv API/官方 PDF 地址；
- 按检索排序自动导入前 N 篇，不设置“候选论文人工筛选”步骤；
- 不接入 OpenAlex、Crossref、Unpaywall 或多来源 OA 下载；
- 部分论文下载或解析失败时允许使用其余成功论文继续；全部失败时终止为 `no_reviewable_papers`。

`N` 是检索与下载预算，不是人工筛选候选数。初始默认值为 10，作为可校准的 Workflow Profile 参数保存到运行快照中。

### 3.3 只在大纲阶段引入 HITL

用户可以：

- `approve`：批准当前大纲并开始章节写作；
- `edit`：提交完整的结构化修订大纲，校验通过后开始写作；
- `feedback`：提交反馈，由模型重新生成大纲并再次暂停。

论文检索、下载和 Evidence Matrix 不引入人工审批。

### 3.4 包含范围

- Review Run、Step、Dependency、Human Input、Output 和 Artifact；
- LangGraph checkpoint、interrupt 和 resume；
- arXiv 检索、下载和项目文献导入；
- Review Run 对 Ingestion/Indexing 子 Run 的等待与恢复；
- Evidence Matrix、结构化大纲、章节生成和引用校验；
- Markdown 综述导出；
- 状态查询、Event/SSE 重放、取消、失败和恢复测试。

### 3.5 非范围

- 候选论文人工筛选、纳排标准 UI；
- 多学术检索源、多 OA 下载源和通用 URL 抓取；
- 自动投稿格式、Word/LaTeX 导出和复杂引用样式切换；
- 事实正确性的通用 LLM Judge；
- Phase 5/6 的 Deep Agents、Browser、MCP 和 Sandbox。

## 4. 模块边界

### 4.1 复用模块

- Project、Paper、PaperVersion、ProjectPaper；
- Ingestion Run 与 Indexing Run；
- Run、RunAttempt、RunEvent、Outbox；
- ChunkSet、Retriever、Evidence、Citation Validator；
- 文件存储、Artifact Storage、SSE/Event Replay。

### 4.2 新增模块

- `ReviewWorkflowService`：创建、查询、取消和恢复 Review Run；
- `ArxivSearchAdapter`：执行受限 arXiv 查询并返回标准化元数据；
- `ArxivProjectImportService`：幂等下载并创建或复用项目论文及子 Run；
- `ReviewDependencyReconciler`：检查论文是否形成可用 ChunkSet，并重新调度父 Run；
- `ReviewGraphFactory`：构建固定 LangGraph；
- `EvidenceMatrixBuilder` 与确定性 Validator；
- `ReviewOutlineService` 与 Human Input 校验；
- `SectionDraftService`、全文一致性检查和 Artifact 导出。

Route 只处理 HTTP、身份和输入输出。应用服务组织授权、事务和外部调用；Domain 保存状态机与不变量；Adapter 承担 PostgreSQL、ARQ、LangGraph、模型、arXiv 和文件存储实现。

## 5. Workflow 定义

```text
validate_request
  → formulate_search_strategy
  → search_arxiv
  → import_arxiv_papers
  → wait_for_ingestion
  → build_evidence_matrix
  → propose_outline
  → persist_outline
  → review_outline
      ├─ approve/edit → draft_sections
      └─ feedback     → propose_outline
  → validate_sections
  → consistency_check
  → export_review
  → finalize
```

### 5.1 节点原则

- 每个节点以稳定 ID 读取业务数据，不把 PDF、全文、完整 Prompt 或大型模型输出放进 Graph State；
- 外部 HTTP、模型调用和文件写入不发生在数据库事务内；
- 有副作用的节点使用确定性幂等键，并在重放时复用已完成结果；
- 节点产物先持久化为业务记录或 Artifact，Graph State 只保存其 ID、版本和小型摘要；
- `review_outline` 节点在调用 `interrupt()` 之前不得执行模型、数据库写入或其他不可重复副作用。LangGraph resume 会从该节点重新执行；
- checkpoint 用于图内恢复，不替代 Run、Event、权限、Artifact 和引用事实。

## 6. Run、Attempt、Outbox 与等待恢复

必须明确区分：

```text
Review Run            用户可查询的完整业务生命周期
Run Attempt           Worker 对该 Run 的一次实际占用/执行历史
Run Event             面向产品、审计和 SSE 的业务时间线
Outbox                当前是否需要把 Run 投递到任务队列
LangGraph State       图内部的小型执行上下文
LangGraph Checkpoint  interrupt/crash 后恢复图执行的位置
```

### 6.1 Run 状态

在现有状态机上增加：

- `WAITING_DEPENDENCY`：等待论文 Ingestion/Indexing 形成可用 ChunkSet；
- `WAITING_INPUT`：等待大纲人工输入。

典型转换：

```text
QUEUED → RUNNING
RUNNING → WAITING_DEPENDENCY
WAITING_DEPENDENCY → QUEUED
RUNNING → WAITING_INPUT
WAITING_INPUT → QUEUED
RUNNING → SUCCEEDED | RETRY_WAIT | FAILED | CANCELLED
```

非法转换必须由 Domain 拒绝。取消可以从排队、运行和两种等待状态发起，并传播到尚未开始的本 Run 工作；是否取消已被其他业务复用的论文子 Run 需由引用关系判断，不能盲目级联。

### 6.2 Attempt 状态

增加 `AttemptStatus.PAUSED` 和 `ExecutionOutcome.PAUSED`：

```text
Run WAITING_INPUT       → 当前 Attempt PAUSED
Run WAITING_DEPENDENCY  → 当前 Attempt PAUSED
Run SUCCEEDED           → 当前 Attempt SUCCEEDED
Run FAILED/RETRY_WAIT   → 当前 Attempt FAILED
Run CANCELLED           → 当前 Attempt CANCELLED
```

等待表示当前 Worker 已正常释放，并非失败或跳过。恢复后由新的 ARQ Job 创建新的 Attempt，旧 Attempt 保留为执行历史。

### 6.3 Outbox 语义

沿用“一条 Run 一条可重置投递记录”，不保存完整投递时间表：

- `run_id` 在 Outbox 中唯一；
- `PENDING` 表示 Dispatcher 尚需投递；
- `DISPATCHED` 表示当前调度请求已送入队列；
- Run 进入等待状态时，Outbox 保持 `DISPATCHED`；
- 正常恢复通过 `schedule_again(run_id)` 将 `DISPATCHED → PENDING`；
- 基础设施失败通过 `reset_for_retry(run_id)` 重置并增加失败/重试计数；
- `schedule_again()` 不增加失败计数，也不受最大失败重试次数限制。

以下内容必须在同一个短事务中提交：

```text
保存依赖完成或 HumanInput
Run WAITING_* → QUEUED
追加 dependency_wait_completed 或 human_input_submitted Event
Outbox DISPATCHED → PENDING
commit
```

Outbox 只表达当前投递需求。完整业务状态变化由 Event 记录，Worker 执行历史由 Attempt 记录；本阶段不额外建设逐次队列投递审计表。

## 7. 父 Run、子 Run 与依赖

Review Worker 下载 PDF 后不直接调用 Ingestion Executor。`ArxivProjectImportService` 为每篇结果：

1. 按 arXiv ID/version 和内容哈希创建或复用 Paper/PaperVersion；
2. 建立或复用 ProjectPaper；
3. 创建或复用 Ingestion Run、对应 Event 和 Outbox；
4. 由现有 Ingestion 流程继续创建 Indexing Run；
5. 建立 Review Run 对论文就绪条件的 `run_dependency`。

幂等键至少包含：

```text
(review_run_id, arxiv_id_or_version, ingestion_profile_version)
```

父 Review Run 等待的是“指定 PaperVersion 已存在可用 ChunkSet”，而不只是某个 Ingestion Run 显示成功。这样可以兼容 Ingestion 后续触发 Indexing、已有索引复用以及子 Run 重试。

```text
Review Worker
  → 创建/复用论文与 Ingestion 子 Run
  → Review Run = WAITING_DEPENDENCY
  → Attempt = PAUSED，Outbox = DISPATCHED

Ingestion/Indexing Worker
  → 生成可用 ChunkSet 或终态失败

Dependency Reconciler
  → 汇总每篇论文就绪/失败状态
  → 达到继续条件时：Review Run = QUEUED
  → Event + schedule_again() 同事务提交

新 Review Worker
  → 载入 checkpoint 与业务依赖结果继续执行
```

至少一篇论文就绪才允许继续；全部论文失败则以 `no_reviewable_papers` 结束。默认最小就绪论文数暂定为 1，并作为校准参数保留。

## 8. LangGraph State、Checkpoint 与版本

Graph State 只保存小型、可序列化字段，例如：

```json
{
  "review_run_id": "...",
  "project_id": "...",
  "research_question": "...",
  "search_strategy_output_id": "...",
  "review_source_ids": ["..."],
  "evidence_matrix_output_id": "...",
  "outline_output_id": "...",
  "approved_outline_output_id": "...",
  "section_output_ids": ["..."],
  "feedback_round": 0
}
```

Checkpoint 的 `thread_id` 必须稳定映射到 Review Run，Resume 必须带上已验证并持久化的 HumanInput，而不是依赖 API 进程内存。

第一版版本格式固定为：

- Workflow：`review.v1`；
- Prompt：`search_strategy.v1`、`evidence_extract.v1`、`outline_generate.v1`、`section_draft.v1`、`consistency_check.v1`；
- Model Profile：`review-default.v1`。

Run 创建时保存 Workflow、Prompt、Model Profile 版本及生效配置快照。名称采用稳定的 `name.vN`，参数调整若影响可复现语义则升级版本；纯部署配置不写进名称，但仍进入快照。

## 9. 数据模型草案

### 9.1 `review_runs`

保存研究问题、Workflow/Profile 版本、配置快照、当前阶段、当前 Output/Artifact 引用和统计摘要；生命周期仍复用通用 Run 表。

### 9.2 `run_steps`

记录稳定 Step key、状态、开始/结束时间、输入输出引用、错误码和幂等键，用于观察节点进度及重放时复用结果。

### 9.3 `review_sources`

记录 Review Run 使用的 arXiv 结果，包括 arXiv ID/version、排序、元数据快照、Paper/PaperVersion ID、导入状态和失败原因。它不是候选筛选表。

### 9.4 `run_dependencies`

记录父 Run、依赖类型、目标 Run/PaperVersion/ChunkSet、状态和满足时间。唯一约束阻止重复依赖。

### 9.5 `human_input_requests` 与输入

记录大纲请求版本、状态、允许动作、创建/解决时间；HumanInput 保存请求 ID、动作、结构化 payload、提交者和幂等键。同一请求只接受一次有效解决。

### 9.6 `review_outputs`

以版本化结构保存 Search Strategy、Evidence Matrix、Outline、Section 和 Consistency Report。重新生成不覆盖旧版本；Run 只指向当前版本。

### 9.7 `artifacts`

保存最终 Markdown、内容哈希、大小、存储位置、创建时间、生成配置与来源 Output 版本。

具体字段和索引在各实现切片开始前通过迁移测试收敛，不在本 Spec 预先固化所有 ORM 细节。

## 10. arXiv 检索、下载与导入

检索策略模型输出：

- 规范化研究问题；
- arXiv 查询表达式；
- 时间、分类等过滤条件；
- 3–6 个结构化分析维度，每个维度包含稳定 `dimension_key`、名称和提取问题。

查询必须经过确定性 Schema 和允许字段校验，不能把任意用户 URL 交给下载器。

下载安全边界：

- 仅允许配置中的 arXiv API 和 PDF 官方 Host；
- 每次重定向后的 Host 仍需位于 allowlist；
- 不携带 Cookie 或用户凭据；
- 设置连接/读取/总超时、最大重定向次数、单文件大小和总下载预算；
- 校验响应类型、PDF magic bytes 和内容哈希；
- 临时网络失败可重试，404、无效 PDF、超限等记录为单篇永久失败。

第一版单文件大小默认沿用导入边界的 50 MiB，其他超时、总预算和并发数作为 `review-default.v1` 的可校准参数。

## 11. Evidence Matrix

### 11.1 目的

Evidence Matrix 是从各论文中提取的结构化中间产物，用于把“论文原文证据”与“章节综合写作”分离。它不是最终综述，也不是单纯的 Citation 列表。

最小行结构：

```json
{
  "paper_id": "paper-1",
  "dimension_key": "method",
  "status": "extracted",
  "finding": "该方法使用分层搜索缩小规划空间",
  "limitations": "只在仿真环境验证",
  "evidence_ids": ["evidence-1", "evidence-2"]
}
```

证据不足也是合法结果：

```json
{
  "paper_id": "paper-1",
  "dimension_key": "real_world_validation",
  "status": "insufficient_evidence",
  "finding": null,
  "limitations": null,
  "evidence_ids": []
}
```

页码、章节、ChunkSet、PaperVersion 和 ParseRevision 通过 `evidence_id` 回查，不在 Matrix 行中重复复制。

### 11.2 固定提取策略

第一版不把三种提取方式暴露为产品配置，固定使用 `review-evidence-extraction.v1`：

1. 若单篇论文所有 Chunk 的估算总量不超过 12,000 tokens，则按文档顺序提供全部 Chunk；
2. 超过阈值时，对每个分析维度使用 Phase 2 Retriever 取 top 5；
3. 将各维度结果合并、去重并按论文顺序排列，总上下文上限 16,000 tokens；
4. 每篇论文只调用一次模型，同时提取全部维度。

阈值、top K 和上限属于 Profile 中的可校准参数，不是第一版用户选项。这样既复用 Phase 2 的检索能力，也避免对“整篇论文 × 每个维度”重复调用带来的成本。

模型上下文包括：

- 研究问题；
- 全部分析维度及其提取问题；
- 当前 Paper/PaperVersion 的可信元数据；
- 受控 Chunk/Evidence 列表，每项带稳定 ID、页码、章节和文本；
- 输出 Schema、只能引用当前输入证据及证据不足处理规则。

### 11.3 Matrix Validator 与失败处理

确定性 Validator 校验：

- JSON/Schema；
- `dimension_key` 必须属于本 Run 的维度集合；
- Paper/PaperVersion/Evidence 必须属于当前用户、Project、Review Run 和当前输入论文；
- `evidence_ids` 必须真实存在，且不能跨论文；
- `extracted` 必须有 finding 和至少一个 Evidence；
- `insufficient_evidence` 不得伪造 finding 或 Evidence；
- 重复行和超限文本。

首次失败后，将结构化错误和原输出交给模型修复一次；仍失败则该论文 Step 以 `evidence_matrix_invalid` 结束。部分论文失败时可在满足最小就绪策略后继续，并在 Run Summary 中披露；全部无法形成有效 Matrix 时 Workflow 失败。

Validator 只保证结构、归属和引用闭包，不宣称自动判断 finding 是否被证据在语义上完全蕴含。

## 12. 大纲和章节写作上下文

### 12.1 大纲

大纲模型接收研究问题、分析维度、Evidence Matrix 的受控摘要和论文覆盖统计，输出：

```json
{
  "sections": [
    {
      "section_key": "methods",
      "title": "主要方法路线",
      "purpose": "比较不同方法及其适用条件",
      "dimension_keys": ["method", "limitations"]
    }
  ]
}
```

持久化后进入 `review_outline` interrupt。`edit` 输入使用同一 Schema 和 Validator；`feedback` 会生成新 Outline 版本并产生新的 Human Input Request。

### 12.2 章节

每个章节写作调用只接收：

- 研究问题；
- 当前已批准 Section 的 title、purpose、dimension_keys；
- 与这些 `dimension_keys` 匹配的 Matrix 行；
- 上述行实际引用的 Evidence 文本与定位信息；
- 已生成前文的短摘要和统一术语表；
- 输出 Schema、引用规则和 token 预算。

章节不会接收全部论文全文，也不会默认接收整个 Matrix。每节保存为版本化 ReviewOutput。完成全部章节后，组装全文并生成统一 ClaimSet，Citation Validator 校验每个重要 Claim 的 Evidence 绑定和可见性。

## 13. Markdown 与引用格式

第一版固定使用数字引用：

```markdown
分层搜索可以缩小规划空间，但现有证据主要来自仿真实验。[1][2]
```

- 文末 References 按首次引用顺序列出；
- 每个编号在系统数据中映射到一篇 PaperVersion 及一个或多个精确 Evidence；
- 前端可由 Evidence 定位到 PDF 页码/章节；
- Markdown Artifact 不依赖隐藏 Prompt 才能解释来源；
- 本阶段不提供 APA、作者-年份等样式切换。

## 14. API 草案

```text
POST   /projects/{project_id}/reviews
GET    /projects/{project_id}/reviews/{run_id}
POST   /projects/{project_id}/reviews/{run_id}/cancel
GET    /projects/{project_id}/reviews/{run_id}/sources
GET    /projects/{project_id}/reviews/{run_id}/evidence-matrix
GET    /projects/{project_id}/reviews/{run_id}/outline
POST   /projects/{project_id}/reviews/{run_id}/outline-input
GET    /projects/{project_id}/reviews/{run_id}/artifacts
GET    /projects/{project_id}/reviews/{run_id}/events
```

创建接口支持 Idempotency-Key。`outline-input` 必须校验用户、Project、Run、当前未解决 Request 和 payload 版本；重复提交同一幂等键返回原结果，不得重复 resume。

## 15. Event 草案

- `review_run_created`
- `search_strategy_completed`
- `arxiv_search_completed`
- `review_source_import_started`
- `review_source_ready`
- `review_source_failed`
- `dependency_wait_started`
- `dependency_wait_completed`
- `evidence_matrix_completed`
- `outline_proposed`
- `human_input_requested`
- `human_input_submitted`
- `section_draft_completed`
- `citation_validation_completed`
- `review_artifact_created`
- `run_succeeded`
- `run_failed`
- `run_cancelled`

业务状态变化和对应 Event 在同一短事务中提交。Event payload 只保存小型摘要和稳定 ID，不记录论文全文、完整 Prompt 或敏感参数。

## 16. Artifact

最终至少生成：

- Markdown 综述；
- 检索策略与分析维度；
- arXiv 检索结果、成功导入和失败清单；
- Evidence Matrix；
- References/引用映射；
- Run Summary：版本、模型、token/调用统计、失败论文和已知限制。

Artifact 写入使用内容哈希和稳定幂等键。Worker 重试不能生成互相冲突的重复最终文件。

## 17. 失败、重试与取消

- arXiv 临时网络错误：节点级有限重试；
- 单篇下载/解析/索引永久失败：记录来源失败，允许其余论文继续；
- 全部论文不可用：`no_reviewable_papers`；
- Matrix Schema/引用失败：修复一次，仍失败记 `evidence_matrix_invalid`；
- 大纲输入版本过期或请求已解决：拒绝，不 resume；
- Worker 在 interrupt 后退出：Attempt `PAUSED`，不是失败；
- Worker 在副作用后崩溃：通过 Step/Output/Artifact 幂等键复用结果；
- Resume Job 重复投递：Run lease、状态条件更新和 checkpoint 保证只产生一次业务效果；
- 取消：节点边界检查取消标记，等待状态可直接进入 CANCELLED。

任何错误不得把模型原始全文上下文、论文全文或 Secret 写入 Event 和普通日志。

## 18. 实现切片

1. [x] 更新状态机、Attempt/Outbox 语义和迁移，先完成等待/恢复事务测试；
2. [ ] 建立 Review Run、Step、Source、Dependency、Output、Human Input 数据契约；
3. [ ] 实现 arXiv 检索与幂等项目导入，复用 Ingestion/Indexing；
4. [ ] 实现依赖等待、Reconciler 和 `schedule_again()` 闭环；
5. [ ] 接入 LangGraph checkpoint，验证 crash recovery；
6. [ ] 实现 Evidence Matrix 固定提取策略与 Validator；
7. [ ] 实现 Outline interrupt、approve/edit/feedback 和 Resume；
8. [ ] 实现章节写作、ClaimSet、Citation Validator 和一致性检查；
9. [ ] 导出 Markdown Artifact，并补齐 API、SSE、取消和端到端测试；
10. [ ] 根据实际代码更新模块学习笔记与阶段完成状态。

每个切片遵循：契约/失败测试 → 最小实现 → 集成测试 → 文档更新。

### 18.1 切片 1 完成记录（2026-08-22）

- `RunStatus` 已增加 `WAITING_INPUT`、`WAITING_DEPENDENCY`；两者均属于
  `ACTIVE_RUN_STATUSES`，支持 `RUNNING → WAITING_* → QUEUED` 和等待中直接取消；
- `AttemptStatus.PAUSED`、`ExecutionOutcome.PAUSED` 已接入。执行器把 Run 推进等待状态后，
  `RunExecutionService` 以 `PAUSED` 正常结束当前 Attempt，Outbox 保持 `DISPATCHED`；
- Outbox Port、Fake 与 SQLAlchemy Adapter 已增加 `schedule_again(run_id)`：只允许
  `DISPATCHED → PENDING`，立即到期、清空 `dispatched_at`，不增加 `attempt_count`；
  `reset_for_retry()` 仍只用于失败重试并增加计数；
- 新增 `WaitingRunResumeService`，按受限的依赖完成或人工输入原因，在同一事务内完成
  `WAITING_* → QUEUED`、原因 Event 和 `schedule_again()`；同时校验 owner 与 Project，错误等待
  状态、重复调用、跨 Project 或 Outbox 不可重置均拒绝提交；PostgreSQL 集成测试确认 Outbox
  条件失败或抛错时 Run/Event 回滚；
- 恢复核心提供不自行提交的 `resume_in_session()`，后续 HumanInput/Dependency 应用服务必须先在
  同一 session 保存原因记录，再调用该方法并由最外层统一 commit；本切片尚无这些业务表，不能
  把“原因记录”和恢复三项操作拆成两个事务；
- 前端状态工具已识别两种等待状态、取消能力、中文文案和两类正常恢复 Event；
- 未新增 Alembic migration：`runs.status`、`run_attempts.status` 均为无数据库枚举/Check
  约束的 `String(20)`，新值不改变表结构；`queue_outbox` 也未增加字段或约束。切片 2 再按实际
  Review 数据契约创建迁移；
- 本切片没有提前增加 `RunType.REVIEW`、Review 数据模型、arXiv 或 LangGraph 实现。
- 已知 crash gap：如果 Run 已提交 `WAITING_*` 或终态，但进程在 best-effort 关闭 Attempt 前崩溃，
  现有 Reconciler 只查询 Run 仍为 `RUNNING` 的 Attempt，无法关闭这条残留 `RUNNING` Attempt；
  Phase 3 切片 5 的 crash recovery 必须补测试并解决，当前不能宣称已有 Reconciler 兜底。

验证结果：Backend 非集成测试 `387 passed, 4 skipped`；完整 PostgreSQL/Valkey integration
`86 passed`；`ruff check src tests` 与 `pyright` 通过；Web Vitest `65 passed`，生产构建通过。

## 19. 重点测试

- Run 和 Attempt 的合法/非法等待状态转换；
- `schedule_again()` 与 `reset_for_retry()` 计数语义不同；
- Run/Event/Outbox 同事务提交与 Dispatcher 至少一次投递；
- 重复 arXiv 结果和重复 Job 不重复建 Paper、子 Run 或 Artifact；
- 父 Run 只在 ChunkSet 可用或依赖已终态汇总后恢复；
- LangGraph interrupt 前无副作用，Resume 使用同一 checkpoint；
- approve、edit、feedback 和重复/过期 HumanInput；
- Evidence Matrix 的跨 Project、跨 Paper、伪造 Evidence 和证据不足校验；
- Section 只能读取对应维度与当前 Run 可见 Evidence；
- ClaimSet 和数字引用映射闭包；
- Worker crash、临时失败、部分论文失败、全部失败和取消；
- SSE 断线后按 Event 序号重放；
- arXiv HTTP allowlist、重定向、超时、大小和无效 PDF。

## 20. 阶段完成条件

- 用户能创建 Review Run，并观察 arXiv 搜索、导入和分析进度；
- Review Run 能等待 Ingestion/Indexing 后自动恢复，不占用 Worker；
- 大纲阶段真实触发 LangGraph interrupt，进程重启后仍可 approve/edit/feedback 并恢复；
- Evidence Matrix 和每个重要 Claim 都能回查到当前 Run 可见 Evidence；
- 最终生成可下载的带数字引用 Markdown Artifact；
- 重复投递、失败重试、取消、部分来源失败和 SSE 重放具有自动测试；
- 普通测试不访问真实 arXiv 和付费模型；
- 阶段 Spec、ADR、总体指南和实际实现保持一致；
- 对实际完成的核心模块补充学习笔记和 60 秒面试说明。

## 21. 已确认与待校准项

### 已确认

- arXiv 单来源自动导入，不做论文人工筛选；
- 只在大纲阶段引入 HITL；
- Evidence Matrix 每篇论文一次调用提取全部维度；
- 长论文使用按维度检索后的合并证据上下文；
- Section 只读取对应维度 Matrix 行和引用 Evidence；
- 一条 Run 一条可重置 Outbox，不保存完整投递历史；
- 正常 Resume 不计入失败重试；
- Markdown 使用 `[1]` 数字引用；
- Workflow/Prompt/Model Profile 使用 `name.vN` 版本。

### 实现前后通过 Fake/小规模真实试验校准

- arXiv 前 N 篇默认 10 是否合适；
- 全文 12,000 tokens、总上下文 16,000 tokens、每维 top 5 的效果与成本；
- 最小就绪论文数默认 1 是否需要提高；
- arXiv 并发、超时、总下载量和依赖 Reconciler 间隔；
- 大纲反馈最大轮数和章节生成 token 预算。

这些参数进入 Profile 配置快照；校准不改变已经确认的产品流程。

## 22. 预计学习笔记

模块实际完成后再创建：

- `docs/learning-journal/modules/arxiv-search-and-paper-import.md`
- `docs/learning-journal/modules/langgraph-checkpoint-and-resume.md`
- `docs/learning-journal/modules/human-outline-review.md`
- `docs/learning-journal/modules/review-evidence-matrix.md`
- `docs/learning-journal/modules/review-artifact-generation.md`
