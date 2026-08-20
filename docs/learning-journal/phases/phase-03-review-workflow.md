# Phase 3：固定文献综述 Workflow

## 状态

待开始。本文为阶段实施 Spec，依赖 Phase 2 已交付稳定的 Model Gateway、Retrieval、Evidence、Citation 和索引能力。

## 目标和用户可见结果

用户输入研究问题后，系统通过一个代码定义、版本化的固定 LangGraph Workflow 搜索文献、受控获取开放全文、等待人工筛选、提取 Evidence、生成并确认大纲，最后输出带引用的 Markdown 综述和研究过程清单。

Workflow 可以在 HTTP 断开、页面刷新或 Worker 重启后恢复；用户可以查看当前 Step、历史 Event、错误、等待事项和最终 Artifact。

```text
研究问题
  → 检索策略
  → 文献搜索与去重
  → 受控下载开放全文并解析/索引
  → 人工筛选
  → Evidence Matrix
  → 主题与大纲
  → 人工确认大纲
  → 分章节撰写
  → 引用和一致性校验
  → Markdown Artifact
```

## 前置条件

- Phase 1 已完成 owner 范围文件去重、ProjectPaper 和跨 Project ParseRevision 复用；
- Phase 2 已完成固定 ParseRevision 的 ChunkSet、Hybrid Retrieval 和 Evidence/Citation；
- Run/Event/Outbox/Attempt/SSE 可以复用；
- 项目已具备 LangGraph 依赖和 Phase 0 学习基础，生产用 PostgreSQL Checkpointer 在本阶段实现；
- 总体实施指南需同步更新“Core v1 不自动下载全文”的旧边界；
- 自动全文获取开始实现前，补一份简短 ADR，明确受控 OA 下载与任意 URL/Browser 的边界。

## 范围决定

### 固定 Workflow

首版只实现一个由代码定义并带版本号的 Review Workflow，不提供可视化 Canvas、用户自定义 DAG 或动态 Agent 规划。

### 受控开放全文获取

Phase 3 允许根据 OpenAlex/Crossref 返回的学术元数据自动获取开放全文，但不接受用户任意 URL，不使用 Browser，不绕过登录、付费墙或 CAPTCHA。

自动获取发生在搜索和元数据去重之后、人工筛选之前，并受候选数、单文件大小和总下载量限制。无法自动获取的候选仍进入筛选页面，用户可以手动上传 PDF、排除或标为 `background_only`。

### 包含

- Review Run、Run Step 和两个人工确认点；
- PostgreSQL LangGraph Checkpoint、Interrupt 和 Resume；
- OpenAlex 搜索与 Crossref DOI/元数据校验；
- 开放全文位置解析、受控 PDF 下载和子 Ingestion Run；
- 文献候选去重、筛选和 ProjectPaper 收录；
- Evidence 提取与 Evidence Matrix；
- 主题、大纲、分章节 Claim/Citation 生成；
- Citation Validator 和简单跨章节一致性检查；
- Markdown、Evidence Matrix 和文献清单 Artifact；
- 最小 Review 创建、筛选、大纲确认、Run Detail 和 Artifact UI。

### 不包含

- 通用 URL 抓取、网页正文解析或 Browser；
- 登录、Cookie、付费墙、CAPTCHA 绕过；
- 任意代码执行、Sandbox 或通用 Tool Registry；
- 用户自定义 Workflow；
- 多 Agent；
- 自动系统性综述质量声明；
- DOCX、复杂图表和完整可观测性平台，这些留给 Phase 4。

## 核心模块和复用边界

Phase 3 复用：

- `DocumentContentReader`、`Retriever`；
- `EmbeddingModel`、`ChatModel`；
- `EvidenceService`、`CitationValidator`；
- ProjectPaper、PaperVersion、ParseRevision、ChunkSet；
- Run、Attempt、Event、Outbox、ARQ 和 SSE；
- Storage。

Phase 3 新增：

- `ReviewWorkflow`：固定 LangGraph 定义；
- `ReviewRunService`：创建、查询和恢复 Review Run；
- `RunStepService`：业务 Step 当前状态；
- `HumanInputService`：筛选和大纲确认；
- `AcademicSearch`：OpenAlex 搜索；
- `MetadataResolver`：Crossref 元数据校验；
- `OpenAccessResolver`：从可信学术元数据产生全文候选；
- `SecurePdfDownloader`：执行受限下载；
- `ReviewArtifactService`：输出 Markdown 和清单。

Review Workflow 不依赖 Conversation 或 RAG Chat，只依赖 Phase 2 的共享应用 Port。

## 首版 Workflow

```text
validate_request
  → formulate_search_strategy
  → search_literature
  → normalize_and_deduplicate
  → acquire_open_full_text
  → wait_for_ingestion
  → prepare_screening
  → [Interrupt 1: 人工筛选]
  → build_evidence_matrix
  → propose_outline
  → [Interrupt 2: 人工确认大纲]
  → draft_sections
  → validate_citations
  → consistency_check
  → export_artifacts
  → finalize
```

首版不做复杂条件分支。没有搜索结果、没有可纳入全文或证据不足时，以稳定业务错误或限制说明收束，不让模型自行改变图结构。

## 业务 Run、Step 和 LangGraph State

必须继续区分：

```text
业务 Run          用户可查询、取消和恢复的任务
Run Step          用户可理解的业务阶段投影
LangGraph State   图内部的小型执行上下文
Checkpoint        图位置、Interrupt 和 Resume 状态
ARQ Job           一次 run_id 投递
```

Run 新增两个非终态：

```text
RUNNING → WAITING_INPUT → QUEUED
RUNNING → WAITING_DEPENDENCY → QUEUED
WAITING_INPUT / WAITING_DEPENDENCY → CANCELLED
```

- `WAITING_INPUT` 用于人工筛选和大纲确认；
- `WAITING_DEPENDENCY` 用于等待下载后的 Ingestion/Indexing 子 Run；
- 等待时当前 Attempt 正常结束并关闭 lease，不计为失败；
- Resume 创建新的 Attempt，通过 Outbox 重新投递同一业务 Run；
- Checkpoint 不能替代 Run、Step、Input、Evidence 或 Artifact 数据。

Step 首版状态为：

```text
pending → running → succeeded
                  ├→ waiting_input
                  ├→ waiting_dependency
                  ├→ failed
                  └→ cancelled
```

## LangGraph State

Graph State 只保存 ID 和小型标量：

```text
run_id
project_id
workflow_version
search_strategy_id
candidate_revision
screening_input_id
selected_project_paper_ids
pinned_parse_revision_ids
evidence_matrix_output_id
outline_output_id
outline_input_id
section_output_ids
artifact_ids
```

PDF、全文、候选大响应、Evidence Matrix、章节正文和 Artifact 内容不进入 Graph State。

每个 Node 使用确定性幂等键。Node 先提交业务结果，再把结果 ID 返回 Graph State；如果业务提交后、Checkpoint 前崩溃，重跑 Node 必须复用已有结果。

## 数据关系

个人项目首版减少表数量，阶段产物使用受控 `review_outputs`，不建设通用 Workflow Output 平台。

```text
Run
└─ ReviewRun(thread_id, workflow_version)
   ├─ RunStep
   ├─ ReviewCandidate
   ├─ HumanInputRequest
   ├─ ReviewOutput(strategy/evidence_matrix/outline/section)
   └─ Artifact
```

主要新增模型：

- `review_runs`：`run_id`、研究问题、workflow/thread/profile 版本；
- `run_steps`：step key、状态、执行次数、时间、错误和输出引用；
- `review_candidates`：外部 ID、规范化元数据、筛选决定、全文获取状态、关联 Paper/Version/子 Run；
- `human_input_requests`：`screening` 或 `outline_approval`、schema 版本、状态和提交内容；
- `review_outputs`：受控 kind、版本、JSONB 小型结构或 Storage Key；
- `artifacts`：owner、Project、Run、kind、content type、storage key、hash 和 size。

Evidence、Claim 和 Citation 继续使用 Phase 2 的表，不在 Phase 3 重复定义。

## 文献搜索、元数据和去重

- OpenAlex 用于候选搜索；
- Crossref 用于 DOI 和书目信息校验；
- 外部请求在数据库事务外执行；
- 每次 Run 保存候选快照，外部 API 后续变化不影响恢复；
- 候选进入人工筛选前不自动成为正式 ProjectPaper；自动获取全文时可以创建或复用 owner-scoped Paper/PaperVersion，但只有人工纳入后才建立 ProjectPaper。现有 Phase 1 `IngestionService` 会立即创建 ProjectPaper，不能直接用于该流程；本阶段需增加只写个人库的 staged/library ingestion 用例，并复用底层校验、去重和解析执行能力。

候选去重优先级：

1. 规范化并验证的 DOI；
2. OpenAlex ID；
3. 标题、年份和第一作者的保守匹配；
4. 无法可靠判断时保留两个候选，不自动合并。

人工纳入后，如果自动获取阶段已经创建或复用了 Paper/PaperVersion，则只新增 ProjectPaper 并选择该版本；尚无全文时可以先保留 metadata-only Paper，待用户上传后再选择 PaperVersion。

## 受控开放全文获取

```text
ReviewCandidate
  → OpenAccessResolver 选择 OA 位置
  → DownloadPolicy 校验
  → SecurePdfDownloader 流式下载到 staging
  → PDF 大小/MIME/Magic Bytes/SHA-256 校验
  → owner 范围内容去重
  → staged/library ingestion 创建或复用子 Run（此时不建 ProjectPaper）
  → 等待 ParseRevision 和 ChunkSet ready
```

下载约束：

- URL 必须来自 OpenAlex/Crossref Adapter 的结构化结果，API 不接受用户 URL；
- 只允许 HTTPS；
- 不携带 Cookie、登录凭据或浏览器状态；
- 拒绝 loopback、私网、link-local 和云元数据地址；
- 每次 Redirect 重新校验目标和 DNS 结果；
- 限制重定向次数、超时、单文件大小、候选数和 Run 总下载量；
- 流式下载并校验 MIME、PDF 文件头和最终哈希；
- HTML 登录页、错误页、需要订阅或 CAPTCHA 的页面不作为 PDF；
- 保存来源 URL、外部 ID、许可证、版本、内容哈希和获取时间；
- 不确定许可证或访问条件时不自动下载，交给用户处理。

自动下载失败不会立即使整个 Review Run 失败。候选记录稳定状态和错误码，在筛选页面允许用户上传、排除或标记 `background_only`。

只有具备成功 ParseRevision 的 `included` Paper 才能进入 Evidence 提取。`background_only` 只能出现在方法或背景清单中，不能支持主要 Claim。

## 父子 Run 和依赖恢复

- Review Run 是父 Run；下载后的解析和索引沿用现有 Run/Event/Worker 执行模型，但通过新的 staged/library ingestion 编排创建，不能直接调用会立即收录 ProjectPaper 的 Phase 1 上传用例；
- 相同候选和获取 profile 只能关联一组有效子 Run；
- 仍有子 Run 未完成时，父 Run 进入 `WAITING_DEPENDENCY` 并保存 Checkpoint；
- 子 Run 完成后，由轻量对账服务检查父 Run 依赖并创建恢复 Outbox；
- 重复通知或对账不会重复 Resume；
- 父 Run 取消后不再创建新子 Run，已有子 Run可协作式取消或允许独立收束，但不能恢复已取消父 Run。

## 人工输入

### 筛选确认

用户对候选选择：

- `included`：正式纳入，必须有可用全文；
- `excluded`：排除并记录原因；
- `background_only`：只保留元数据，不用于主要 Claim。

提交时校验 Project、候选版本和全文就绪状态。有效提交在一个事务中写入 Decision、ProjectPaper、Input 状态、Run `WAITING_INPUT → QUEUED`、Event 和 Outbox。

### 大纲确认

用户可以批准、拒绝并给出反馈，或提交编辑后的结构化大纲。每次修改产生新的 Outline Output 版本，已消费的 InputRequest 不能重复 Resume。

两类提交都使用 `Idempotency-Key`；相同请求返回同一结果，不同 Payload 返回冲突。

## Evidence-first 生成

Evidence 提取按研究问题和检索策略中的维度执行：

```text
Selected Papers
  → Phase 2 Retriever（额外限制 Paper 集合）
  → Structured Evidence Extraction
  → Evidence Validator
  → Evidence Matrix
  → Topics / Outline
  → Section Claims + Citations
```

- 不把全部论文一次性放入模型上下文；
- 每篇论文和每个章节都有上下文上限；
- Evidence 保存 PaperVersion、ParseRevision、Chunk、页码和章节；
- 每个章节独立生成并持久化，可幂等重试；
- Citation Validator 通过后章节才能完成；
- 一致性检查只报告冲突、遗漏和重复，不凭空补充无 Evidence 的事实；
- 证据不足的章节应明确写出限制。

## Artifact

Phase 3 最少生成：

- Markdown 综述；
- 检索策略；
- 纳入文献清单；
- 排除文献及原因清单；
- Evidence Matrix JSON 或 CSV；
- 简单书目数据；
- Run 摘要报告。

Artifact 提交使用 Run + kind 的确定性幂等键，重复导出不会产生多个“最终版本”。Markdown 中的引用必须能跳转到系统内 Evidence/PDF 来源。

## API 方向

```text
POST /api/v1/projects/{project_id}/review-runs
GET  /api/v1/projects/{project_id}/review-runs
GET  /api/v1/review-runs/{review_run_id}

GET  /api/v1/runs/{run_id}/steps
GET  /api/v1/review-runs/{review_run_id}/candidates
GET  /api/v1/review-runs/{review_run_id}/inputs
POST /api/v1/review-runs/{review_run_id}/inputs/{input_id}

GET  /api/v1/review-runs/{review_run_id}/evidence-matrix
GET  /api/v1/review-runs/{review_run_id}/outline
GET  /api/v1/review-runs/{review_run_id}/sections

GET  /api/v1/projects/{project_id}/artifacts
GET  /api/v1/artifacts/{artifact_id}
GET  /api/v1/artifacts/{artifact_id}/content
```

Run 查询、取消、Event 和 SSE 继续复用通用接口。

## Event 方向

新增事件至少包括：

- `workflow_started`、`step_started`、`step_completed`、`step_failed`；
- `search_strategy_created`、`academic_search_completed`、`candidate_set_created`；
- `full_text_acquisition_started`、`full_text_acquisition_completed`；
- `dependency_wait_started`、`dependency_wait_completed`；
- `human_input_requested`、`human_input_submitted`；
- `evidence_matrix_completed`；
- `outline_created`、`outline_approved`；
- `section_drafted`、`citation_validation_completed`；
- `artifact_created`、`workflow_completed`。

Event 只保存 ID、状态、数量、错误码和 Usage 摘要，不保存搜索大响应、论文全文、Prompt、Evidence Matrix 或完整 Draft。

## 失败、重试和取消

- OpenAlex/Crossref 429、5xx、超时按临时外部错误处理；
- 无搜索结果以稳定业务错误等待用户修改研究问题；
- 下载临时失败可以有限重试，安全拒绝、非 PDF、超限和登录墙为永久获取失败；
- 某些候选下载失败不影响其他候选；
- 缺少全文停在筛选阶段，不进行基础设施重试；
- 模型结构化输出最多修复一次；
- Node 业务结果已提交但 Checkpoint 丢失时，重跑复用原结果；
- 重复 Resume 由 Input 状态和幂等键拒绝；
- `WAITING_INPUT` / `WAITING_DEPENDENCY` 可直接取消；
- RUNNING 取消在 Node、外部请求和章节边界检查；
- 取消后临时 Draft 可以保留内部记录，但不提交最终 Artifact；
- Checkpoint、业务 Run 和 Artifact 的恢复所有权必须分别测试。

## 实现切片顺序

1. **阶段契约**：固定 Workflow、State、Node、错误码和测试 Fixture；
2. **Run/Step 等待状态**：`WAITING_INPUT`、`WAITING_DEPENDENCY` 和状态机测试；
3. **LangGraph 骨架**：PostgreSQL Checkpointer、最小图、Interrupt/Resume；
4. **Human Input**：筛选/大纲输入、幂等提交和恢复 Outbox；
5. **学术搜索**：OpenAlex、Crossref、候选快照和去重；
6. **开放全文获取**：Resolver、安全下载、内容去重、staged/library ingestion 和子 Run；
7. **依赖等待**：父子 Run 对账、重复通知、取消和恢复；
8. **筛选闭环**：候选 UI、全文状态、ProjectPaper 收录；
9. **Evidence Matrix**：受控 Retrieval、结构化提取和验证；
10. **大纲闭环**：主题、大纲版本和第二次人工确认；
11. **分章节撰写**：Claim/Citation、独立重试和一致性检查；
12. **Artifact**：Markdown、Evidence Matrix 和文献清单；
13. **最小 Web UI**：Review 创建、Step Timeline、输入和 Artifact；
14. **验收复盘**：故障注入、E2E、评测和学习笔记。

## 测试方式

- **Domain**：Run/Step 等待状态、Input 生命周期、候选决定和 Artifact 幂等；
- **LangGraph**：Node 路由、两个 Interrupt、重复 Resume 和 Checkpoint 恢复；
- **Application**：父子 Run、业务提交后 Checkpoint 丢失、取消和重复执行；
- **HTTP Adapter**：OpenAlex/Crossref 正常、分页、429、5xx、超时和坏响应；
- **Downloader**：Redirect、私网地址、超限、HTML 登录页、非 PDF 和响应丢失；
- **PostgreSQL/Worker**：Run/Step/Input 唯一约束、Worker 重启和依赖对账；
- **Evidence/Citation**：跨 Project 拒绝、无来源 Claim 和章节重复执行；
- **E2E**：创建 Review → 自动获取/手动补全文 → 筛选 → 大纲确认 → 下载 Markdown。

普通测试使用 Fake Chat/Embedding、HTTP Mock 和合成 PDF，不访问实时学术 API 或付费模型。真实 OpenAlex/Crossref、模型和开放 PDF 只用于显式 Smoke/Evaluation。

## 阶段完成条件

- 固定 Workflow 由代码定义并记录版本；
- 两个人工节点可以暂停、刷新和恢复；
- 等待人工或子 Run 时不占用 Worker lease；
- Worker 重启后可以从 PostgreSQL Checkpoint 恢复；
- 重跑不重复创建 Paper、Evidence、章节或 Artifact；
- 开放全文下载受固定来源、网络和文件策略限制；
- 相同文件和 Paper 跨 Project 复用解析与索引；
- 每个业务 Step 在 Run Detail 可观察；
- 每个主要 Claim 可追溯到 Evidence 和 PDF 页码；
- 输出包含检索策略、纳入/排除清单和 Markdown 综述；
- 外部 API、下载、模型、Checkpoint、取消和重复 Resume 有故障测试；
- 阶段 Spec、模块学习笔记、E2E 证据和已知限制已更新。

## 实现前需要确定

以下参数在对应切片前确定，不改变阶段边界：

1. 首版自动搜索和下载的候选数量上限；
2. 允许自动下载的 OA 状态、许可证和来源策略；
3. OpenAlex/Crossref 请求预算、分页和缓存时间；
4. Evidence Matrix 的最小字段；
5. 大纲结构、章节数量上限和 Markdown 引用样式；
6. Workflow Definition、Prompt 和 Model Profile 的版本格式。

## 预期学习笔记

- `docs/learning-journal/modules/academic-search-and-full-text-acquisition.md`；
- `docs/learning-journal/modules/langgraph-checkpoint-and-resume.md`；
- `docs/learning-journal/modules/human-in-the-loop.md`；
- `docs/learning-journal/modules/review-evidence-matrix.md`；
- `docs/learning-journal/modules/review-artifact.md`。
