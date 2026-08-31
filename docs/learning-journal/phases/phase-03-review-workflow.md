# Phase 3：可暂停恢复的固定文献综述 Workflow

## 状态

- 当前状态：已完成（2026-08-23，切片 1–10 已通过阶段收尾审计）
- 需求讨论：2026-08-22 已完成第一轮收敛
- 关联决策：[ADR-0003：Phase 3 固定文献综述 Workflow](../decisions/0003-phase-3-fixed-review-workflow.md)

### 2026-08-31 产品表达演进

Project 工作区将 `/reviews` 的用户可见名称调整为“文献研究”。Evidence Matrix 是固定 Workflow 的主要
分析结果，Markdown 综述是后续产物，不再用最终文件名称概括整条检索、导入、解析、索引和证据整理链路。
同时，Project 文献库新增独立的 arXiv 搜索与引入入口，复用既有受限 Adapter 和 Ingestion 管线。

本次演进不改变 `review.v1` 的持久状态机与历史重放语义：旧 Profile 仍会自动搜索并导入来源。独立文献库
检索产生普通 Project Paper 与 Ingestion Run，不创建 Review Run；未来若新增“只消费项目既有文献”的
Review Profile，必须另行版本化契约，不能静默改变 `review.v1`。

### 2026-08-31 `review.v2` 来源选择决策

新建文献研究任务改用版本化的 `review.v2`；已有 `review.v1` Run、Event、Checkpoint 和恢复入口保持只读
兼容。`review.v2` 在创建时允许从当前 Project 选择 0–3 个已固定的 PaperVersion，并可选择“来源不足时
检索 arXiv 候选”。最终进入 Evidence Matrix 的来源上限仍为 3，但候选检索预算独立固定为 10，避免把
最终来源上限误当成人工筛选候选数。

关键不变量如下：

- 0 个已选来源且关闭候选检索时拒绝创建 Run；1–2 个来源且关闭检索时允许以不足 3 篇继续；
- Project 来源必须属于当前 owner/Project，创建时固定 PaperVersion，且必须已有 ready ChunkSet；
- 自动补充只负责检索候选，不自动导入；Workflow 在下载和导入前进入来源筛选 HITL；
- 用户最多确认 `3 - 已选来源数` 个候选，也可不再补充并用已有来源继续；没有任何有效来源时不能越过
  来源筛选节点；
- arXiv 候选与用户选择都持久化并可幂等重放；未选候选不得创建 Project Paper、Ingestion Run 或下载
  正式 PDF；
- 摘要来自 arXiv 元数据；页数优先从作者提交的 arXiv comment（如 `18 pages`）提取。comment 未声明时
  显示“页数待下载后确认”，筛选前不为补齐页数而下载 PDF，也不得因此自动导入或丢弃候选；
- 来源确认后复用既有解析、索引、Evidence Matrix、大纲 HITL、章节写作、引用校验与导出链路。

```text
研究问题 + 0–3 个 Project PaperVersion + 候选检索策略
  → 校验并固定 Project 来源
  → （需要补充）生成检索策略并搜索最多 10 个 arXiv 候选
  → 来源筛选 HITL
  → 仅导入确认候选
  → 等待 Ingestion/Indexing
  → Evidence Matrix
  → 大纲 HITL
  → 分章节写作、校验与导出
```

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

### 3.2 只使用 arXiv；v1 自动纳入，v2 人工筛选

- 论文发现、元数据和 PDF 下载统一使用 arXiv API/官方 PDF 地址；
- `review.v1` 按检索排序自动导入前 N 篇；`review.v2` 检索最多 10 个候选，并在下载前等待人工筛选；
- 不接入 OpenAlex、Crossref、Unpaywall 或多来源 OA 下载；
- 部分论文下载或解析失败时允许使用其余成功论文继续；全部失败时终止为 `no_reviewable_papers`。

`review.v2` 的最终来源上限与候选检索预算分别为 3 和 10，并共同保存在运行快照中。

### 3.3 顺序 HITL

`review.v2` 先使用 `select_sources` 完成候选筛选；随后复用大纲阶段的以下动作：

用户可以：

- `approve`：批准当前大纲并开始章节写作；
- `edit`：提交完整的结构化修订大纲，校验通过后开始写作；
- `feedback`：提交反馈，由模型重新生成大纲并再次暂停。

论文下载和 Evidence Matrix 不引入额外审批；来源筛选解决后才允许下载选中候选。

### 3.4 包含范围

- Review Run、Step、Dependency、Human Input、Output 和 Artifact；
- LangGraph checkpoint、interrupt 和 resume；
- arXiv 检索、下载和项目文献导入；
- Review Run 对 Ingestion/Indexing 子 Run 的等待与恢复；
- Evidence Matrix、结构化大纲、章节生成和引用校验；
- Markdown 综述导出；
- 状态查询、Event/SSE 重放、取消、失败和恢复测试。

### 3.5 非范围

- 复杂纳排标准、批量自动评分和多轮筛选 UI；
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
- `ReviewSearchStrategyService`：生成、强校验并幂等持久化固定检索策略；
- `ArxivSearchAdapter`：执行受限 arXiv 查询并返回标准化元数据；
- `ArxivProjectImportService`：幂等下载并创建或复用项目论文及子 Run；
- `ReviewDependencyReconciler`：检查论文是否形成可用 ChunkSet，并重新调度父 Run；
- `ReviewExecutor` 与 `ReviewGraphFactory`：编排图外依赖等待，并构建/恢复固定 LangGraph；
- `EvidenceMatrixBuilder` 与确定性 Validator；
- `ReviewOutlineService` 与 Human Input 校验；
- `SectionDraftService`、全文一致性检查和 Artifact 导出。

Route 只处理 HTTP、身份和输入输出。应用服务组织授权、事务和外部调用；Domain 保存状态机与不变量；Adapter 承担 PostgreSQL、ARQ、LangGraph、模型、arXiv 和文件存储实现。

## 5. Workflow 定义

```text
validate_request
  → formulate_search_strategy
  → search_arxiv
  → review_sources（review.v2 且需要补充时）
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

生产实现把外部检索、下载、子 Run 创建和依赖等待放在 `ReviewExecutor` 的图外业务编排层；只有
Outline HITL 到章节、引用、一致性、导出和终态收敛进入持久 LangGraph。这避免把释放 Worker 的
`WAITING_DEPENDENCY` 伪装成 LangGraph Interrupt。来源筛选是图外顺序 HITL，大纲确认仍使用 LangGraph
Interrupt。每次恢复会先重放
Search Strategy、arXiv 导入和 Matrix 应用服务，但这些服务先复核持久 Step/Output/Source，因此不会
重复调用模型、arXiv 或创建业务副作用。执行器只在明确查询到 Thread 尚无 checkpoint 时调用
`start(initial_state)`；已有 checkpoint 一律 `resume`，损坏或非法 checkpoint 稳定失败，不能被当作
首次执行覆盖。

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
- `WAITING_INPUT`：等待来源筛选或大纲人工输入。

典型转换：

```text
QUEUED → RUNNING
RUNNING → WAITING_DEPENDENCY
WAITING_DEPENDENCY → QUEUED | FAILED
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

实际实现沿用现有 Ingestion/Indexing 的分层幂等身份，不再额外发明一个与现有 Profile 脱节的
`ingestion_profile_version`：

```text
检索完成事实       = (review_run_id, search_query_hash)
Review Source      = (review_run_id, arxiv_id, arxiv_version)
PaperVersion       = (owner_id, pdf_sha256)
ParseRevision      = (paper_version_id, parse_profile_hash)
ChunkSet           = (parse_revision_id, chunk_profile_hash)
```

因此同一 Review Run 不会重复纳入同一 arXiv 版本，相同 PDF 在 owner 范围内复用，而解析器或切块配置
变化仍会由真实 Profile hash 产生新的 Revision/ChunkSet；这比只保存一个名义版本号更贴合既有执行链。

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
  "claim_set_id": "...",
  "consistency_output_id": "...",
  "final_output_id": "...",
  "final_artifact_id": "...",
  "feedback_round": 0
}
```

Checkpoint 的 `thread_id` 必须稳定映射到 Review Run，Resume 必须带上已验证并持久化的 HumanInput，而不是依赖 API 进程内存。

第一版版本格式固定为：

- Workflow：`review.v1`；
- Prompt：`search_strategy.v1`、`review-evidence-extraction.v1`、`outline_generate.v1`、`section_draft.v1`、`consistency_check.v1`；
- Model Profile：`review-default.v1`。

Run 创建时保存 Workflow、Prompt、Model Profile 版本及生效配置快照。名称采用稳定的 `name.vN`，参数调整若影响可复现语义则升级版本；纯部署配置不写进名称，但仍进入快照。

## 9. 数据模型契约

### 9.1 `review_runs`

保存研究问题、Workflow/Profile 版本、配置快照、当前阶段、当前 Output/Artifact 引用和统计摘要；生命周期仍复用通用 Run 表。

`current_stage` 表示下一个正在执行或等待的固定业务阶段，不是只在 LangGraph 内更新的装饰字段。创建
事务已经完成请求校验，因此同时保存成功的 `VALIDATE_REQUEST` Step，并把 Stage 置为
`FORMULATE_SEARCH_STRATEGY`。图外 Strategy、arXiv search/import、依赖等待/恢复和 Matrix 服务分别
在其 Step 与对应 Event 的同一短事务中推进 Stage；重放只复用既有事实，不允许把 Outline/Section 等
后期 Stage 倒退。详情 API 因而可以在 LangGraph 启动前持续展示真实进度。

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

保存最终 Markdown、检索策略、来源清单、Matrix、完整 References 映射和 Run Summary 的内容哈希、
大小、存储位置、创建时间、生成配置与来源 Output 版本。正文只写入 Storage，数据库不保存文件内容。

### 9.8 切片 2 已落地的约束

- `review_runs.run_id` 同时是主键和通用 `runs.run_id` 外键；研究问题、Workflow/Prompt/
  Model Profile 版本、配置快照、受控统计摘要和当前阶段都持久化，通用 Run 继续拥有生命周期与
  Project/owner；统计摘要只包含固定来源/模型调用/token 计数，不作为万能 JSON；
- `run_steps` 以 `(run_id, sequence)` 固定展示顺序，以 `(run_id, idempotency_key)` 阻止
  重放时重复副作用；输入和输出 JSON 只保存稳定业务引用，不用于保存正文；
- `review_sources` 以 `(review_run_id, arxiv_id, arxiv_version)` 和
  `(review_run_id, rank)` 去重，Paper/PaperVersion 在完成导入前允许为空；
- `run_dependencies` 不使用无可靠 FK 的多态 `target_id`。它使用 `target_run_id`、
  `target_paper_version_id`、`target_chunk_set_id` 三个显式可空 FK，通过 Check 保证类型与唯一
  非空目标一致，并为三类目标分别建立部分唯一索引；
- `review_outputs` 只提供追加写入，`(review_run_id, output_type, output_key, version)` 与
  `(review_run_id, idempotency_key)` 唯一。结构化 JSON 受 256 KiB 领域上限保护；更大的 Markdown
  或矩阵导出必须进入 Artifact Storage；
- `human_input_requests` 以 `(review_run_id, request_version)` 版本化，并以部分唯一索引保证
  同一 Review Run 最多一个 `open` 请求；`human_inputs.request_id` 唯一，且
  `(request_id, request_version)` 复合 FK 拒绝过期版本；同一提交者的幂等键也唯一；
- `artifacts` 只保存 Storage Key、SHA-256、大小、MIME、来源 Output 和小型 metadata，不保存
  文件正文；Storage Key 与 `(review_run_id, idempotency_key)` 唯一；
- 对外读取由 `ReviewRepository` 统一带上 `run_id + project_id + owner_id`，通过通用 Run 联表
  校验范围。写入方法只供已经授权的应用服务或 Worker 使用，此内部边界在 Port 中明确说明。

数据库在本切片只保证目标存在、受限类型和主要唯一性，不保证所有跨聚合引用都属于同一业务范围。
后续写入服务必须在同一事务中校验：ReviewRun 的当前 Output/Artifact、ReviewSource 的
`paper_id`/`paper_version_id` 配对、HumanInputRequest 的 `resolved_input_id`、Artifact 的
Project/owner/来源 Output，均与目标 Review Run 一致。前三个 current/resolved 指针会形成循环引用，
迁移先建表再添加真实 FK；不能把“有 FK”表述为已经验证上述归属。

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
4. 每篇论文先做一次正常模型调用，同时提取全部维度；仅在输出非法时追加一次修复调用。

阈值、top K 和上限属于 Profile 中的可校准参数，不是第一版用户选项。这样既复用 Phase 2 的检索能力，也避免对“整篇论文 × 每个维度”重复调用带来的成本。

模型上下文包括：

- 研究问题；
- 全部分析维度及其提取问题；
- 当前 Paper/PaperVersion 的可信元数据；
- 受控 Chunk/Evidence 列表，每项带稳定 ID、页码、章节和文本；
- 输出 Schema、只能引用当前输入证据及证据不足处理规则。

分析维度不接受节点调用方临时传入，而是从当前 Review Run 范围内、Schema 固定为
`search-strategy.v1` 的 `SEARCH_STRATEGY` Output 加载；第一版固定为 3–6 个维度。进入 Prompt
前，所选 Chunk 会先固化为属于当前 Review Run 的 Evidence，Prompt 与 Matrix 都使用该持久
`evidence_id`，不使用只存在于上下文中的临时编号。READY 来源不得重复指向同一 Paper，因为最小
Matrix 行不携带 `source_id`/`version_id`，重复 Paper 无法被后续章节唯一解释。

### 11.3 Matrix Validator 与失败处理

确定性 Validator 校验：

- JSON/Schema；
- `dimension_key` 必须属于本 Run 的维度集合；
- Paper/PaperVersion/Evidence 必须属于当前用户、Project、Review Run 和当前输入论文；
- `evidence_ids` 必须真实存在，且不能跨论文；
- `extracted` 必须有 finding 和至少一个 Evidence；
- `insufficient_evidence` 不得伪造 finding 或 Evidence；
- 重复行和超限文本。

第一版每个 `finding`/`limitations` 最多 500 字符、每行最多 10 个不重复 Evidence；聚合 Output
额外限制为 240 KiB，给 `ReviewOutput` 的 256 KiB 领域上限留出余量。

首次失败后，将结构化错误和原输出交给模型修复一次；仍失败则以稳定 per-paper Output 持久化
`evidence_matrix_invalid`。若后续论文遇到临时模型或数据库错误、导致聚合 Output 尚未提交，节点重放
必须识别该失败 Output 并跳过已经永久失败的论文。部分论文失败时可在满足最小就绪策略后继续，并在
Run Summary 中披露；全部无法形成有效 Matrix 时 Workflow 失败。

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

`outline.v1` 第一版安全边界固定为：1–12 个章节；`section_key` 使用最多 64 字符的
snake_case；`title` 最多 200 字符，`purpose` 最多 1,000 字符；每节关联 1–6 个不重复且属于
Search Strategy 的维度；整体 JSON 不超过 64 KiB。人工 `feedback` 是去除首尾空白后 1–4,000
字符的纯文本。`feedback_round` 必须非负且单调递增，但本切片尚不设置最大反馈轮次预算。

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
GET    /projects/{project_id}/reviews/{run_id}/artifacts/{artifact_id}/content
GET    /projects/{project_id}/reviews/{run_id}/events
```

创建接口支持 Idempotency-Key。`outline-input` 必须校验用户、Project、Run、当前未解决 Request 和 payload 版本；重复提交同一幂等键返回原结果，不得重复 resume。
实际 Route 统一带 `/api/v1` 前缀。Review 的 Event 游标读取使用上述 Project-scoped `events`；实时 SSE
复用通用且同样执行 owner 隔离的 `GET /api/v1/runs/{run_id}/events/stream`，通过
`Last-Event-ID` 从 PostgreSQL Event 序号之后重放。

## 15. Event 草案

- `review_run_created`
- `search_strategy_completed`
- `arxiv_search_completed`
- `review_source_import_started`
- `review_source_import_completed`
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
2. [x] 建立 Review Run、Step、Source、Dependency、Output、Human Input 数据契约；
3. [x] 实现 arXiv 检索与幂等项目导入，复用 Ingestion/Indexing；
4. [x] 实现依赖等待、Reconciler 和 `schedule_again()` 闭环；
5. [x] 接入 LangGraph checkpoint，验证 crash recovery；
6. [x] 实现 Evidence Matrix 固定提取策略与 Validator；
7. [x] 实现 Outline interrupt、approve/edit/feedback 和 Resume；
8. [x] 实现章节写作、ClaimSet、Citation Validator 和一致性检查；
9. [x] 导出 Markdown Artifact，并补齐 API、SSE、取消和端到端测试；
10. [x] 根据实际代码更新模块学习笔记与阶段完成状态。

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

### 18.2 切片 2 完成记录（2026-08-22）

- `RunType.REVIEW` 已加入通用 Run 契约；新增 `ReviewWorkflowService.create_review_run()`，在一个
  事务中校验 active Project 与 owner，并创建通用 Run、`review_runs`、
  `review_run_created` Event、Outbox 和通用 IdempotencyKey；同键同请求回放原 Run，同键不同请求
  冲突。HTTP Route 留到切片 9，但后续 API 必须要求 `Idempotency-Key` 并复用该服务；
- 新增不可变领域契约与受限枚举：ReviewRun、RunStep、ReviewSource、ReviewDependency、
  ReviewOutput、HumanInputRequest/HumanInput 和 Artifact；固定版本使用 `name.vN` 校验，受控 JSON
  有大小上限，ReviewRun 统计摘要只使用固定计数键，Artifact 不接收文件正文；
- 新增单一 Alembic migration `a8c3e5f7b9d1`，创建 `review_runs`、`run_steps`、
  `review_sources`、`run_dependencies`、`review_outputs`、`human_input_requests`、
  `human_inputs` 和 `artifacts`，并落地 FK、Check、顺序、版本与幂等唯一约束；迁移已在独立
  PostgreSQL 数据库实际执行 `upgrade head → downgrade -1 → upgrade head`；
- 新增 `ReviewRepository` Port、SQLAlchemy Adapter 与应用测试 Fake。所有公开查询方法同时要求
  `run_id`、`project_id`、`owner_id`；Output Repository 不提供覆盖旧版本的方法；
- 本切片只建立数据契约和 Review Run 最小创建闭环，没有实现 Review API、arXiv HTTP、Source
  导入状态推进、Dependency Reconciler、LangGraph、Evidence Matrix、HumanInput 提交/恢复或
  Artifact 文件写入；这些行为仍按后续切片顺序开发；
- HumanInput “同一请求只能解决一次”当前由领域状态机、`human_inputs.request_id` 唯一约束、
  请求版本复合 FK 和单 open 请求部分唯一索引共同固定。切片 7 仍需实现带行锁的
  “保存 Input + 解决 Request + 恢复 Run”事务，不能只依赖前端禁用按钮。
- 数据库当前只保证引用目标存在和主要唯一性；ReviewRun 当前 Output/Artifact、ReviewSource 的
  Paper/PaperVersion 配对、Request 的 resolved Input，以及 Artifact 的 Project/owner/来源 Output
  归属，必须由对应后续写服务在同一事务中校验，不能依赖 FK 自动保证同一 Review Run 范围。

验证结果：切片 2 定向领域/应用测试 `11 passed`、定向 PostgreSQL 集成测试 `7 passed`；Backend
完整非集成测试 `398 passed, 4 skipped`，完整 PostgreSQL/Testcontainers integration
`93 passed`；`ruff check src tests` 与 `pyright` 通过。迁移还在独立 PostgreSQL 数据库通过
`upgrade head → downgrade -1 → upgrade head`。本切片没有前端改动，因此未重复运行 Web 测试与构建。

实现和取舍详见
[Review Workflow 数据契约](../modules/review-workflow-data-contracts.md)。

### 18.3 切片 3 完成记录（2026-08-22）

- 新增确定性的 `ArxivSearchQuery`：只接受 arXiv 允许字段、受限分页与排序，拒绝 URL、控制字符和
  未允许字段；本切片不生成检索策略，模型输出留给后续固定图节点；
- 新增 `HttpxArxivGateway`，只访问官方 API/PDF Host。Atom 结果保持 API rank、按 arXiv
  ID/version 去重并截断；历史 `http` 官方 PDF 地址规范化为 `https`，Feed 注入的非官方 URL 会在
  下载前拒绝；
- Atom Feed 与 PDF 都使用流式累计读取：Feed 默认限制 2 MiB；PDF 在每次重定向后重新验证 scheme、
  Host、凭据和 `/pdf/` 路径，并限制 50 MiB 单文件与剩余总预算；两者都先验证 Content-Type 与
  Content-Length，PDF 另校验 `%PDF-` magic bytes 和 SHA-256。
  timeout、transport、429 和 5xx 最多尝试三次，耗尽后仍作为临时错误上抛供 Run 重试；404、非法
  PDF、Host 和超限以稳定单篇错误码落库并继续；
- `DownloadedPdf` 直接构造也必须满足 PDF MIME、magic bytes 与 `content_hash == SHA-256(content)`；
  Adapter 的大小、重定向、尝试次数和官方 Host 子集，以及 Service 总下载预算均在构造时 fail-fast，
  不能通过配置关闭边界或扩成任意 Host；
- `search_sources()` 先在短事务中验证 owner/Project/Review Run，再出网，并在提交事务重新锁定范围。
  Source、`arxiv_search_completed` Event 与 `SEARCH_ARXIV` 成功 Step 同事务保存；查询指纹 Step 让
  有结果和零结果都能回放，不重复出网、Event 或 Source；
- PDF 下载和 Storage 写入均在数据库事务外。缓存键固定为
  `{owner_id}/arxiv-cache/sha256/{sha256}.pdf`，不包含标题或未经清理的 arXiv 文本；数据库失败时
  保留可复用/对账缓存，不执行可能删除并发执行者文件的补偿删除；
- 每篇登记使用短事务创建或复用 Paper、PaperVersion、ProjectPaper、Ingestion Run、`run_created`
  Event 和 Outbox，同时绑定 ReviewSource 与依赖；现有 Ingestion Worker/Executor 继续创建 Indexing
  Run，本服务不直接调用 Executor。已有 ready ChunkSet 时通过 Revision→Version 连接验证归属并建立
  satisfied PaperVersion/ChunkSet 依赖；
- 同 owner+SHA-256 首次登记使用 PostgreSQL 事务级 advisory lock 配合既有唯一索引，不同 Review
  Run 并发导入会收敛为一套 Paper/Version/Ingestion Run/Outbox。复用 Version 的旧 Ingestion Run
  只有同时匹配 owner、当前 Project 和 `RunType.INGESTION` 才成为 RUN 依赖；跨 Project 时只保留
  PaperVersion 依赖供切片 4 对账；
- 已归档 Paper 不会被自动恢复或绑定为后续 RAG 不可见的 ready Source，而以
  `review_source_paper_archived` 稳定失败；该行为与现有 ProjectLibrary/Phase 1 语义一致；
- `ReviewRepository` 增加 scoped source 行锁与受控保存，`ChunkSetRepository` 增加按 Version 查询
  ready ChunkSet，`PaperVersionRepository` 增加 owner+hash 事务锁；未新增数据库表或迁移。

验证结果：最终补强后的 arXiv 领域/HTTP Adapter/应用定向测试 `47 passed`，定向 PostgreSQL
并发与回滚测试 `2 passed`；Backend 完整非集成测试 `446 passed, 4 skipped`，完整
PostgreSQL/Testcontainers integration `95 passed`；`ruff check src tests`、`pyright` 与
`git diff --check` 通过。本切片没有前端改动，因此未重复运行 Web 测试与构建。

实现和取舍详见
[arXiv 检索与项目导入](../modules/arxiv-search-project-import.md)。

### 18.4 切片 4 完成记录（2026-08-22）

- 新增 `ReviewDependencyWaitService`：持锁校验 owner、Project、Review 类型和扩展记录后，在一个短事务
  中提交 `RUNNING → WAITING_DEPENDENCY` 与 `dependency_wait_started` Event；Outbox 保持
  `DISPATCHED`，现有 `RunExecutionService` 因而把当前 Attempt 正常关闭为 `PAUSED`；
- `ReviewDependency` 增加单向 `PENDING → SATISFIED | FAILED` 领域转换；Run 状态机最小增加
  `WAITING_DEPENDENCY → FAILED`，用于依赖已经全部终态却无法继续时的正常业务终止，不复用包含
  Attempt 计数与失败重试语义的 `apply_run_failure()`；
- 新增有界 `ReviewDependencyReconciler`。Worker 独立周期循环先扫描等待父 Run，再逐个锁定父 Run
  二次检查；两个 Reconciler 命中同一 Run 时由行锁与状态条件串行化，重复轮次不会重复 Source
  Event、恢复或 Outbox 重置；单候选异常回滚后不阻塞同批其他候选，该循环与现有 lease Reconciler
  分开捕获异常和关闭；
- 来源是否 ready 只以其 PaperVersion 下真实 `READY` ChunkSet 为准。对账同时校验 Paper、Version、
  ProjectPaper、owner 和 Revision 归属；同 Project 的 RUN 依赖只接受匹配 owner/Project 的
  Ingestion Run，跨 Project 复用 Version 按切片 3 契约只观察 PaperVersion/ChunkSet，不要求 RUN
  依赖；
- Ingestion/Indexing 仍活跃时继续等待；终态失败会把 Source 和对应 Dependency 推进为 FAILED；
  Ingestion 成功却缺 Revision、Indexing Run，或 Indexing 成功却没有 ready ChunkSet，均转成稳定的
  单篇不变量错误，不让父 Run 永久悬挂；
- Evidence 集采用“全部 Source 都成为 `READY`/`FAILED` 后才决定”的固定边界，不因第一篇成功提前
  恢复。默认 `minimum_ready_papers=1`；至少一篇 ready 时在同一事务中保存 Source/Dependency、
  追加 `review_source_*` Event，再调用 `resume_in_session(DEPENDENCY_COMPLETED)` 完成
  `WAITING_DEPENDENCY → QUEUED`、`dependency_wait_completed` 与
  `schedule_again(DISPATCHED → PENDING)`；全部不可用以 `no_reviewable_papers` 终止。快照若显式
  配置更高最小值且最终不足，则使用 `insufficient_reviewable_papers`；
- 中间轮次若只确认部分来源失败/就绪，会提交对应 Event 并在 commit 后发送轻量 SSE 通知，但返回
  计数仍只统计本轮恢复或终止的父 Run；通知失败仍由既有轮询兜底；
- PostgreSQL 集成测试确认两个并发 Reconciler 只有一次恢复；`schedule_again()` 抛错时 Source、
  Dependency、Event、Run 与 Outbox 整体回滚；未新增表、列或 Alembic migration。

验证结果：切片 4 领域/应用/Worker 定向测试 `33 passed`，定向 PostgreSQL 事务/并发/回滚测试
`3 passed`；Backend 完整非集成测试 `457 passed, 4 skipped`，完整
PostgreSQL/Valkey/Testcontainers integration `98 passed`；`ruff check src tests`、`pyright` 与
`git diff --check` 通过。本切片没有前端改动，因此未重复运行 Web 测试与构建。

实现和取舍详见
[Review 论文依赖等待与恢复](../modules/review-dependency-reconciliation.md)。

### 18.5 切片 5 完成记录（2026-08-22）

- 固定 `review.v1` Thread 为 `review.v1:review-run:{review_run_id}`；LangGraph 1.2.10 根图实际使用
  空 `checkpoint_ns`（该字段保留给子图），因此版本通过 Thread 前缀和 metadata 隔离；Graph State
  只允许小型结构和稳定业务 ID；
- `ReviewWorkflowRuntime` 严格区分首次 `start(initial_state)` 与 `resume(review_run_id)`；恢复使用
  `ainvoke(None)`，已完成 Thread 重复恢复不会开启新一轮节点；
- 固定官方 `langgraph-checkpoint-postgres==3.1.1`；连接使用 `autocommit=True`、`dict_row` 和显式
  生命周期，Serializer 禁止 pickle 并使用严格 allowlist，运行时不调用 `setup()`；
- Alembic 创建与官方 migrations 0–9 对齐的 checkpoint 表和索引；真实 PostgreSQL 测试用两个全新
  连接、Checkpointer 和 Runtime 证明 pending task 跨实例恢复、Thread 隔离及副作用重放幂等；
- `RunReconcileService` 新增残留 Attempt 收敛：保护 `RUNNING`/`CANCEL_REQUESTED` 最新 Attempt，
  按候选 Attempt 时间区间内的持久 Event 区分 PAUSED/FAILED，不根据 Run 类型猜测；并发和重复
  Reconciler 只有一次效果。`CANCEL_REQUESTED` 在心跳新鲜时继续等待 Worker，lease 过期则在同一
  事务将 Run/Attempt 收敛为 CANCELLED 并写 Event，不进入失败重试或重置 Outbox；
- checkpoint 数据库故障映射为临时错误，版本/缺失恢复状态映射为永久数据错误；节点业务
  `ValueError`/`TypeError` 保持原样，避免错误分类越过 Runtime 边界；
- 本切片未把 Review Executor 注册进生产 Worker：Evidence、Outline、Artifact 尚未实现，现在接线会
  无输出地虚假成功。切片 6 完成首个真实节点后再评估；`interrupt()`/HumanInput 保留给切片 7。

验证结果：Backend 完整非集成测试 `475 passed, 4 skipped`；完整 PostgreSQL/Valkey integration
`102 passed`；Alembic 实际执行 `upgrade head → downgrade -1 → upgrade head`，随后 `alembic check`
确认无新 upgrade 操作；`ruff check`（`src`、`tests`、迁移环境与本切片 revision）、`pyright` 和
`git diff --check` 均通过。普通测试未调用真实模型或 arXiv。

实现和取舍详见
[LangGraph Checkpoint 与崩溃恢复](../modules/langgraph-checkpoint-and-crash-recovery.md)。

### 18.6 切片 6 完成记录（2026-08-22）

- 新增 `ReviewEvidenceMatrixService` 和确定性 Matrix Validator。服务只接受 `RUNNING` Review Run，
  从当前 owner/Project/Run 范围内、Schema 为 `search-strategy.v1` 的 Search Strategy Output 加载
  3–6 个稳定维度，并校验 READY Source、PaperVersion、成功 ParseRevision、READY ChunkSet 和 Chunk
  的完整归属；重复 Paper Source 被拒绝；
- 短论文按 Chunk 文档顺序提供不超过 12,000 estimated tokens 的全文；长论文为每个维度调用 Phase 2
  `Retriever.retrieve_for_scope()` top 5，以精确 `(paper_id, version_id)` SQL 范围检索，应用层再次
  复核返回版本和 ChunkSet，再合并去重、按文档顺序排列并限制为 16,000 tokens；每篇论文先做一次
  全维度正常提取调用，仅在输出非法时最多追加一次修复调用；
- 选中 Chunk 在模型调用前以当前 Review Run 的 Evidence 固化。PostgreSQL 使用
  `(run_id, chunk_id)` 唯一键和 `ON CONFLICT DO NOTHING` 收敛并发；回读行必须与预期
  Run/Project/Paper/Version/ParseRevision/Chunk/定位和摘录一致。Matrix 因而只引用可持久回查的
  `evidence_id`，不引用 Prompt 临时 ID；
- Prompt 固定为 `review-evidence-extraction.v1`，只传研究问题、全部维度、白名单 Paper/Version
  元数据和受控 Evidence/Chunk context。此前创建服务中的旧名 `evidence_extract.v1` 已按 ADR 修正；
  这只改变修正后新建 Review Run 的请求指纹，不迁移既有快照；
- Validator 校验严格 Schema、完整维度集、Paper 与 Evidence 的 Run/Project/Paper/Version 闭包、
  重复行/引用、字段类型、状态语义和大小边界。`insufficient_evidence` 是合法结果；首次无效后在
  原始受控上下文中追加结构化错误，仅修复一次，仍失败以稳定 per-paper Output 持久化
  `evidence_matrix_invalid`；即使后续论文临时失败且聚合尚未提交，重放也会跳过该永久失败论文并保留
  `paper_failures`。部分论文失败继续并写入聚合摘要，全部失败则以永久错误结束总 Step；
- 单篇成功、单篇永久失败和聚合 `ReviewOutput` 均使用稳定幂等键，回读既有行时复核完整稳定语义；
  聚合提交前的重放按单篇 Output 跳过成功或永久失败论文，最终聚合 Output 提交后的重放则先验证闭包
  并直接返回。总
  `BUILD_EVIDENCE_MATRIX` Step 只允许条件式 `PENDING → RUNNING → SUCCEEDED|FAILED`，聚合 Output、
  Step 成功和 `evidence_matrix_completed` Event 在同一事务提交，终态 Step 不会被旧执行者回退；
- 当前固定 Step 枚举只有一个总 `BUILD_EVIDENCE_MATRIX`，因此未伪造“每篇论文 Step”；单篇失败以
  聚合 Output 的 `paper_failures` 保存稳定错误码。生产 Review Executor 仍未注册：Search Strategy
  节点和切片 7 的 Outline/interrupt 尚未形成完整图，此时把 Matrix 节点接到 `END` 会让 Run
  无大纲与 Artifact 地虚假成功；
- 未新增数据库结构或 Alembic migration；普通测试使用 Fake Model/Retriever，不调用真实 Provider
  或 arXiv。

验证结果：切片 6 领域与应用定向测试 `49 passed`，定向 PostgreSQL 幂等集成测试 `3 passed`；
Backend 完整非集成测试 `508 passed, 4 skipped`，完整 PostgreSQL/Valkey integration
`105 passed`；`ruff check src tests`、`pyright` 与 `git diff --check` 均通过。完整集成首次运行暴露
`review_outputs` 两条唯一约束的并发竞争，修复为任一唯一冲突均回读既有行并交由应用层比较稳定语义，
随后定向与完整集成复跑通过。普通测试未调用真实模型或 arXiv；本切片没有数据库结构变化。

实现和取舍详见
[Review Evidence Matrix](../modules/review-evidence-matrix.md)。

### 18.7 切片 7 完成记录（2026-08-23）

- 新增 `ReviewOutlineService` 与 `outline.v1` 确定性 Validator。模型上下文只包含研究问题、3–6 个
  Search Strategy 维度、重新通过 Phase 6 Validator 的 Matrix 受控摘要、覆盖统计和持久反馈文本；
  不包含 Chunk/PDF 全文、Evidence ID、未知 Source metadata 或模型原始历史；
- Outline Prompt 固定为 `outline_generate.v1`。Output 使用反馈轮次形成稳定版本和幂等键；模型调用
  发生在事务外，Output 先提交，随后 Request/等待事务提交。若进程在 Output 后崩溃，重放复用
  Output，不再次调用模型；
- 持锁等待事务原子保存/复用 OPEN `HumanInputRequest`，推进 ReviewRun current outline/stage，暂停
  `REVIEW_OUTLINE` Step，完成 Run `RUNNING → WAITING_INPUT`，并追加 `outline_proposed`、
  `human_input_requested` 两个 Event。首轮 `PROPOSE_OUTLINE`/`PERSIST_OUTLINE` Step 保留 v1 Output
  引用；反馈轮由追加式 Output、Request 和 Event 观察，不伪装覆盖首轮 Step；
- `HumanOutlineInputService` 严格校验 owner/Project/Run、当前 OPEN Request、request/output 版本、允许
  action 和 payload。`approve` 只接受空 payload；`edit` 使用同一 Outline Validator 并追加新版本；
  `feedback` 只接受有界文本。HumanInput、Request resolve、可选 edit Output/current pointer、Step 推进、
  `human_input_submitted` Event、Run `WAITING_INPUT → QUEUED` 与 Outbox
  `DISPATCHED → PENDING` 在同一事务提交，正常恢复不增加失败计数；
- 同一提交者幂等键同语义回放原 HumanInput，异义冲突；同一 Request 只解决一次。Request 行锁、条件
  更新、`human_inputs.request_id` 唯一约束和 Outbox 条件重置共同收敛并发双提交；Outbox 失败会回滚
  HumanInput、Request、Step、Run 和 Event；
- `review_outline` LangGraph Node 在 `interrupt()` 之前只读取 Graph State 并构造小型 interrupt 值，
  不执行模型、数据库、Event 或其他副作用。崩溃恢复继续使用 `ainvoke(None)`；真正 HITL 使用
  `Command(resume={request_id,human_input_id})`。后继节点按持久 Request/HumanInput/Outline Output
  再次复核闭包，不能信任 Resume payload；
- approve/edit 更新 `approved_outline_output_id` 并到达切片 8 的安全占位边界，不把 Review Run
  虚假完成；feedback 单调增加 `feedback_round`、从持久 HumanInput 加载反馈、生成下一 Outline/
  Request 并再次 interrupt。真实 PostgreSQL Checkpointer 验证了跨 Runtime/连接重启后的 Command
  Resume；
- 未新增数据库结构或 Alembic migration。生产 Review Executor 仍未注册：切片 8 的章节、引用、
  一致性和 Artifact 链路尚未完成，当前把安全占位边界接到生产执行会虚假成功。反馈最大轮次预算仍
  待 Profile 校准。

验证结果：最终完整非集成回归为 `546 passed, 4 skipped`，完整 PostgreSQL/Valkey integration 为
`108 passed`；最后闭包与范围审查后的 Outline 领域、应用、MemorySaver Graph、Step 和重试策略定向
回归为 `59 passed`，HumanInput 事务与真实 PostgresSaver restart/Command Resume 集成为 `4 passed`；
`ruff check src tests`、`pyright` 与 `git diff --check` 均通过。

实现和取舍详见
[人工大纲确认与恢复](../modules/human-outline-review.md)。

> 2026-08-28 校准：以下切片 8 的 `review-default.v1`/4000 记录保留为当时事实。当前新建 Run 已使用
> `review-default.v3`，固定 `source_limit=3` 和章节/一致性预算 8000/8000；2026-08-29 的 Real
> 截断复现促成从 v2 升级到 v3。执行链仍兼容历史 v1 和既有 v2 快照。详见 ADR-0003
> 的 Profile 校准补充。

### 18.8 切片 8 完成记录（2026-08-23）

- 新增 `section.v1`：每节按批准 Outline 顺序生成，固定保存 `section_key/title/status/summary/claims/
  terminology`。`answered` 的每个 Claim 必须绑定 1–10 个不重复 Evidence；
  `insufficient_evidence` 不得生成 Claim。每节最多 50 个 Claim 和 50 个术语，摘要、Claim、术语及
  Output 均受已有 ReviewOutput 大小边界约束；
- `section_draft.v1` 的每次模型调用只接收研究问题、当前批准 Section、命中该 Section 维度的 Matrix
  行、这些行实际引用的 Evidence 定位与摘录、已完成前文的短摘要、统一术语字典、Schema、引用规则
  和配置快照中的输出 token 预算。不传整篇论文、完整 Matrix、失败清单或无关 Evidence；章节按顺序生成，
  因此前文摘要和术语只来自已经持久化且重新校验的 Section Output；
- 新建 `review.v1` 的 Profile 快照固定包含 `section_output_token_limit=4000` 和
  `consistency_output_token_limit=2000`，字段随创建请求指纹持久化；切片 8 之前创建且缺少字段的开发
  Run 使用相同 v1 默认值兼容。显式字段必须是整数，章节范围 256–16,000、一致性范围 256–8,000；
  `section.v1` 原始模型 JSON 在 Pydantic 解析前限制为 192 KiB，`consistency-report.v1` 限制为
  64 KiB，并继续受 ReviewOutput 256 KiB 总上限保护；
- 每节使用稳定 Output key/幂等键追加 `SECTION` ReviewOutput；Output 与
  `section_draft_completed` Event 在同一持锁短事务提交。节点重放先回读、验证并复用既有 Output，
  不再次调用模型；所有章节完成后，`DRAFT_SECTIONS` Step 和 Stage 推进到引用校验；
- 复用 Phase 2 的 `ClaimSet/Claim/Citation` 表和基础 Citation Validator，但将领域语义从
  RAG-only 泛化为一次生成结果。PostgreSQL Repository 增加原子 get-or-add：ClaimSet 按 `run_id`、
  Claim 按 `(claim_set_id, sequence)`、Citation 按复合主键收敛；写入后回读数据库赢家 ID，并比较
  完整 Claim 文本和 Citation 集合，拒绝同幂等身份的异义结果，不破坏原有显式 `add_*` 的 Phase 2
  唯一约束测试；
- Review Citation Validator 先执行 Phase 2 的非空/零引用/伪造/重复/跨 Run 规则，再逐条复核
  Matrix row 的 Paper 与当前 READY ReviewSource、owner 下 PaperVersion、当前成功 ParseRevision、
  Project/Run Evidence 闭包。校验成功时 ClaimSet、Claim、Citation、`VALIDATE_SECTIONS` Step、Stage
  和 `citation_validation_completed(passed=true)` Event 同事务提交；失败时持久化 FAILED Step 与
  `passed=false`/稳定 reason counts Event，并阻断一致性和导出，不增加模型 repair；
- 章节入口不只信任 Graph State：每次加载均反查 `BUILD_EVIDENCE_MATRIX` 与 `REVIEW_OUTLINE` 已成功且
  Output refs 分别命中本次 Matrix/批准 Outline；Validate 还反查成功的 Draft Step，Consistency 反查
  成功的 Validate Step 与 ClaimSet。三个新 Step 的 input refs 固化依赖 Output IDs、Section IDs、
  ClaimSet ID 及 Prompt/Schema/Validator 版本，重放逐字段比较；
- 所有创建/完成 Step、提交 Section/Consistency Output、ClaimSet/Citation Event 和失败收尾事务均先
  持锁复核 Run 仍为当前 owner/Project 的 RUNNING Review。模型返回后若已请求取消，不提交新 Output、
  Event 或 Stage；取消终态仍由切片 9 Executor 统一收敛。成功新增 Citation Event 在 commit 后发送
  轻量通知，纯重放不重复 Event/通知；
- `consistency_check.v1` 只读取结构化 Section 的摘要、Claim 文本和术语，输出版本化
  `consistency-report.v1`。`terminology/contradiction/redundancy` issues 是非阻断报告，不触发模型重写；
  合法 issues 完成 Step 并把 Stage 推进到 `EXPORT_REVIEW`；Schema 非法会稳定标记 Step 失败，
  Scope 或模型调用失败会阻断本次执行并交给既有 Worker 错误分类/重试，不会进入导出；
- 固定 LangGraph 已扩展为 approve/edit → `draft_sections` → `validate_sections` →
  `consistency_check` → 切片 9 安全边界。Graph State 只增加 Section Output、ClaimSet 和 Consistency
  Output ID；生产 Review Executor 仍不注册，避免在 Markdown Artifact 尚未导出时虚假成功；
- 未新增 migration：现有 ReviewOutput 类型 Check 已含 `section/consistency_report`，ClaimSet 三表也
  已具备所需唯一约束。新增 Repository 方法是在这些约束上实现原子 get-or-add。

切片定向验证：章节领域/应用及 Review 创建契约 `31 passed`，与 Phase 2 Citation/RAG 回归
`47 passed` 合并为 `78 passed`；PostgreSQL
ClaimSet 并发与既有 Evidence/Claim 回归 `7 passed`；补充 ClaimSet/Review Stage PostgreSQL 定向回归
`4 passed`。根审查定位并修复了异步图内同步 Interrupt/条件路由经线程执行器恢复后无法继续调度的
问题：两者改为 async callable 后，approve 与 feedback 再次 interrupt 的真实 Resume 测试均通过，
Graph 全文件回归为 `11 passed`。Ruff 与 Pyright 通过。
切片收尾时完整非集成回归为 `575 passed, 4 skipped`，完整 PostgreSQL/Testcontainers
集成回归为 `111 passed`。

实现和取舍详见
[综述章节写作、引用校验与一致性报告](../modules/review-section-citation-consistency.md)。

### 18.9 切片 9 完成记录（2026-08-23）

- 补齐切片 3–8 遗留的生产入口：新增最小固定 `ReviewSearchStrategyService`，严格复用 Run 冻结的
  `search_strategy.v1`、`search-strategy.v1` 和 `review-default.v1`。模型输出有 64 KiB 前置大小
  限制、extra-forbid Schema、arXiv 查询 allowlist、3–6 个唯一维度及字段长度校验；不引入 repair。
  外部模型调用在事务外，返回后持锁复核 RUNNING owner/Project Review；Step、Output、Event 使用稳定
  幂等键，重放复用持久 Output，不再次调用模型；
- 新增生产 `ReviewExecutor` 并注册 `RunType.REVIEW` Worker。检索、下载、子 Run 和依赖等待继续在图外
  应用服务执行，`WAITING_DEPENDENCY` 正常结束 Attempt；零结果或全部永久失败稳定抛出
  `no_reviewable_papers`。至少一篇 READY 后进入 Matrix 与固定 LangGraph；Outline 仍是唯一 Interrupt。
  Runtime 先明确检查 checkpoint 是否存在，只有不存在时 `start`，已有或损坏状态不能被 broad catch
  当作首次执行；
- `review.v1` 固定图现已连接 approve/edit → Section → Citation → Consistency → Export → Finalize。
  Graph State 只新增 ClaimSet、Consistency Output、Final Output 和最终 Artifact ID；节点每次以业务库
  重新复核范围。Finalize 在同一短事务内完成 Step、Run `RUNNING → SUCCEEDED` 和 `run_succeeded`
  Event，外层 `RunExecutionService` 只按终态关闭 Attempt，不重复写终态 Event；
- 导出器从已验证 Section/ClaimSet/Citation/Evidence/READY Source 构造 Markdown。正文中每个 Claim
  使用 `[1][2]`，论文编号按全文首次引用顺序稳定分配；完整映射保存 Paper/PaperVersion/Source、arXiv
  版本、Evidence、Claim 和页码/章节定位的 Bibliography Artifact。Final Output 只保存引用数量和
  Artifact manifest，不把可能增长的完整映射复制回 256 KiB ReviewOutput。固定生成 Markdown、
  Search Strategy、Source Manifest、
  Evidence Matrix、Bibliography 和 Run Summary 六类 Artifact；Summary 披露 Workflow/Prompt/Profile/
  配置、统计、来源失败及已知限制。导出时从已有 `model_invocations` 审计事实聚合
  模型调用与 token，并与来源计数一起固化到 Final Output、Run Summary 和
  `review_runs.statistics_summary`，不另建重复计数体系；
- Artifact 内容先用 SHA-256 稳定 Storage Key 写入事务外 Storage，再在持锁短事务中以类型级幂等键
  收敛元数据、Final Output、Stage 和 `review_artifact_created` Event。写文件后取消只留下可复用缓存，
  不提交 Artifact/Event/Stage；数据库提交前崩溃重放会覆盖同字节缓存并收敛为一套 Artifact/Event；
  下载时重新校验大小和哈希；
- 新增 `/api/v1/projects/{project_id}/reviews` 创建、详情、取消、sources、evidence-matrix、outline、
  outline-input、artifacts、Artifact content 和 events API。Route 只调用应用服务；所有读取统一验证
  owner+Project+Run 三元范围。创建和 HumanInput 强制 `Idempotency-Key`；Artifact 下载返回内容哈希
  `ETag`；实时通知与断线恢复复用通用 owner-scoped SSE/`Last-Event-ID`；
- Fake Chat Model 增加固定 Review Schema 响应；普通测试不访问真实 arXiv 或付费 Provider。本切片未
  新增数据库结构或 migration，复用切片 2 的 Artifact/Output/Step 唯一约束，并新增 Artifact
  PostgreSQL 并发 get-or-add 与 final pointer 范围测试。根审查另将策略 Schema、章节范围/输出、
  Citation 和导出闭包错误纳入集中永久错误分类，避免对确定性非法结果做无意义重试。

验证结果：导出/策略/执行器/完整图定向测试 `26 passed`，Review Finalize/Attempt 收尾定向测试
`1 passed`，Review API 与通用 SSE 定向测试 `10 passed`，新增 PostgreSQL Artifact 并发与范围集成
测试 `1 passed`；Backend 完整非集成回归 `596 passed, 4 skipped`，完整
PostgreSQL/Valkey/Testcontainers 集成回归 `112 passed`；
`ruff check src tests` 与 `pyright` 通过。本切片只更新为“切片 1–9 已完成”，不会提前执行切片 10
的阶段最终完成审计。

实现和取舍详见
[综述 Artifact 生成与生产执行闭环](../modules/review-artifact-generation.md)。

### 18.10 切片 10 完成记录（2026-08-23）

- 逐条复核阶段完成条件、生产 Worker 组装、Project-scoped API、Event/SSE、数据库事实、LangGraph
  checkpoint/interrupt/resume、Artifact、取消、错误分类、幂等与 owner/Project 隔离；切片 1–9 的
  垂直闭环和测试证据均存在，没有以文档勾选替代实现；
- 收尾审计发现详情 API 暴露的 `ReviewRun.current_stage` 在图外阶段缺少推进：新建 Run 可能在
  Strategy、arXiv search/import、依赖等待和 Matrix 期间长期显示 `validate_request`。修复后创建事务
  同时保存成功的 `VALIDATE_REQUEST` Step，并把 Stage 置为 `FORMULATE_SEARCH_STRATEGY`；Strategy、
  Search、Import、Wait/Reconcile 和 Matrix 分别在其 Step/Event 短事务中推进到下一个真实阶段；
- arXiv 导入增加稳定 `IMPORT_ARXIV_PAPERS` Step 和 `review_source_import_completed` Event。无需等待的
  READY fast path 原子进入 `BUILD_EVIDENCE_MATRIX`；需要子 Run 的路径以 PAUSED Wait Step 进入
  `WAIT_FOR_INGESTION`，Reconciler 在 `dependency_wait_completed` 恢复事务内完成 Step 并进入 Matrix；
  Matrix Output、Step、`evidence_matrix_completed` 与 `PROPOSE_OUTLINE` Stage 同事务提交；
- Stage 推进使用预期前置值条件更新。Strategy/arXiv/Matrix 重放复用持久结果；已进入
  `PROPOSE_OUTLINE`、Section 或更晚阶段时，Import/Wait/Matrix 不能把 Stage 倒退。新增 Fake 与
  PostgreSQL 断言覆盖初始校验 Step、fast path、等待恢复、Matrix 完成和后期 Stage 重放；
- 最终文档统一为实际行为：Run Summary 的模型调用与 token 从 `model_invocations` 审计事实聚合；
  Final Output 只保存引用计数和六类 Artifact manifest，完整引用映射只保存在 Bibliography Artifact
  及 Claim/Citation/Evidence 事实中；早期模块笔记中已完成的“后续切片”限制不再冒充当前状态；
- 本阶段仍保留真实小样本参数校准、前端 Review 专用页面、Checkpoint/缓存保留清理和更强语义
  Groundedness 评测。这些不破坏后端固定 Workflow 的完成条件，归入 Phase 4 产品闭环与评测。

最终验证：Backend 完整回归 `708 passed, 4 skipped`；单独重跑 PostgreSQL/Valkey/Testcontainers
集成目录 `112 passed`；Ruff 与 Pyright 均通过，`git diff --check` 通过。普通测试全程使用 Fake
Provider/HTTP Mock，不访问真实 arXiv 或付费模型。

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
- 后续 Output/Source/HumanInput/Artifact 写服务拒绝跨 Review Run、跨 Project、跨 owner 的引用配对；
- Worker crash、临时失败、部分论文失败、全部失败和取消；
- SSE 断线后按 Event 序号重放；
- arXiv HTTP allowlist、重定向、超时、大小和无效 PDF。

## 20. 阶段完成条件

- [x] 用户能创建 Review Run，并观察 arXiv 搜索、导入和分析进度；
- [x] Review Run 能等待 Ingestion/Indexing 后自动恢复，不占用 Worker；
- [x] 大纲阶段真实触发 LangGraph interrupt，进程重启后仍可 approve/edit/feedback 并恢复；
- [x] Evidence Matrix 和每个重要 Claim 都能回查到当前 Run 可见 Evidence；
- [x] 最终生成可下载的带数字引用 Markdown Artifact；
- [x] 重复投递、失败重试、取消、部分来源失败和 SSE 重放具有自动测试；
- [x] 普通测试不访问真实 arXiv 和付费模型；
- [x] 阶段 Spec、ADR、总体指南和实际实现保持一致；
- [x] 对实际完成的核心模块补充学习笔记和 60 秒面试说明。

## 21. 已确认与待校准项

### 已确认

- arXiv 单来源自动导入，不做论文人工筛选；
- 只在大纲阶段引入 HITL；
- Evidence Matrix 每篇论文一次正常调用提取全部维度，非法时最多一次修复；
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

## 22. 学习笔记进度

模块实际完成后再创建：

- 已完成：`docs/learning-journal/modules/arxiv-search-project-import.md`（已从预计列表移出）；
- 已完成：`docs/learning-journal/modules/review-dependency-reconciliation.md`；
- 已完成：`docs/learning-journal/modules/langgraph-checkpoint-and-crash-recovery.md`；
- 已完成：`docs/learning-journal/modules/review-evidence-matrix.md`；
- 已完成：`docs/learning-journal/modules/human-outline-review.md`；
- 已完成：`docs/learning-journal/modules/review-section-citation-consistency.md`；
- 已完成：`docs/learning-journal/modules/review-artifact-generation.md`。
