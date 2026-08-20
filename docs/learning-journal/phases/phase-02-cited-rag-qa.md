# Phase 2：有引用的 RAG 文献问答

## 状态

进行中。实施前方案已于 2026-08-20 与用户逐项讨论确认，结论见文末「已确定事项」。本文为阶段实施 Spec，建立在 Phase 1 完成 Project、文献库、可靠导入和结构化 Element 层之后。

## 目标和用户可见结果

用户可以在一个 Project 内创建 Conversation 并提问，系统只检索该 Project 已收录且索引就绪的论文，生成带 Paper、版本和页码引用的回答。用户刷新页面后仍能看到问题、回答、引用和执行状态，并可从引用跳转到原始 PDF 来源。

```text
Project Question
  → Hybrid Retrieval
  → Evidence Context
  → 结构化 Answer + Evidence IDs
  → Citation Validator
  → 持久化 Message / Claim / Citation
```

Phase 2 交付可复用的 Model Gateway、Retrieval、Evidence 和 Citation 能力。Phase 3 通过这些应用 Port 生成综述，不依赖 Conversation 或 RAG Chat 页面。

入口与归档语义遵循 `../decisions/0002-archive-and-project-scoped-entrypoints.md`：RAG 始终属于 Project，Paper/多选 Paper 只是 Project 内的检索范围。

## 前置条件

以下收口均已在 Phase 1 完成（2026-08-20 核实）：

- 同一 `owner_id` 范围内按 PDF SHA-256 去重，不跨用户复用私有文件；
- Paper 不直接属于 Project，通过 `ProjectPaper` 建立收录关系；
- `ProjectPaper` 显式保存 `selected_version_id`；
- 相同 PaperVersion 的 ParseRevision 可被多个 Project 复用；
- 个人库与 Project Paper 列表、固定 Version 的 document/elements/file API 可供 Chat、Retrieval 与引用跳转使用。首版不要求独立 Paper detail 或版本历史 API；若 Chat UI 产生明确消费需求，再作为 Phase 2 垂直切片补充。

## 范围决定

### 包含

- 从固定 ParseRevision 生成版本化 ChunkSet；
- PostgreSQL 全文检索和 pgvector 精确向量检索；
- 简单 Hybrid 合并、每篇论文结果上限和上下文预算；
- Embedding/Chat Model Port、Fake Provider 和一个 OpenAI-compatible Adapter；
- Conversation、Message 和后台 `rag_answer` Run；
- Evidence、Claim、Citation 和确定性 Citation Validator；
- Run/Event/SSE 进度展示和最终回答恢复；
- 最小 RAG Chat、Evidence 详情和 PDF 页码跳转；
- 小型固定 Retrieval/Citation 评测集。

### 不包含

- OpenAlex/Crossref 搜索和自动全文下载；
- LangGraph 和 Review Workflow；
- HNSW、独立向量数据库、独立 reranker 或复杂 Query Expansion；
- 多模型路由、模型管理平台和用户自定义 Prompt；
- Conversation 分支、自动摘要和长期记忆；
- Token 级实时输出。首版通过 SSE 展示 Run 进度，回答在结构化生成和引用校验成功后一次性持久化；
- DOCX、图表和 Review Artifact。

## 核心边界

- `DocumentQueryService`（Phase 1 已落地的授权读路径，即早期文档所称的 `DocumentContentReader`，2026-08-20 确认承认现状、不新建独立 Port）：按授权上下文读取指定 ParseRevision 的 Element 和来源定位；Worker 内索引构建已知 `revision_id`，直接走 `ElementRepository` 读取，不重复授权链；
- `ChunkBuilder`：把 Element 组合成适合检索的 Chunk；
- `EmbeddingModel`：批量生成向量并返回 Usage；
- `ChatModel`：生成结构化 Answer/Claim/Evidence ID；
- `Retriever`：执行 Project-scoped Hybrid Retrieval；
- `EvidenceService`：把检索结果固化为可引用 Evidence；
- `CitationValidator`：校验 Claim 与 Evidence 的结构和权限关系；
- `RagAnswerService`：编排 Message、Retrieval、模型调用和最终提交。
- Conversation 必须绑定 `project_id`，默认 scope 为整个 Project 或 Project 内选中的 Paper；每个回答 Run 在启动时固化实际 `selected_version_ids`，保证历史可重放。

Phase 2 不引入 LangGraph。ARQ Job 仍只携带 `run_id`；Phase 1 的 Worker 尚无 run_type 分发机制，本阶段在 Worker 装配处新增按 `run_type` 显式分发的组合 Executor，并在领域层引入 `RunType` 枚举（当前 `run_type` 是无约束字符串），未知类型显式失败，不被静默执行。分发后调用 `ingestion`、`indexing` 或 `rag_answer` Executor。

## 数据关系

首版保持模型简单：一个 ChunkSet 同时固定 Chunk Profile 和 Embedding Profile，模型变化时创建新的 ChunkSet，不拆分独立的向量索引平台。

```text
DocumentParseRevision
└─ ChunkSet(profile_hash, status)
   └─ Chunk
      ├─ ChunkElementLink → DocumentElement
      ├─ search_vector
      └─ embedding

Project
└─ Conversation(scope_mode: project | selected_papers)
   ├─ ConversationScopePaper(paper_id, version_id)   # selected_papers 模式下的默认范围
   ├─ User Message
   └─ Assistant Message
      └─ ClaimSet
         └─ Claim ── Citation ── Evidence ── Chunk
```

主要新增模型：

- `chunk_sets`：`parse_revision_id`、chunk/embedding profile、状态、错误和完成时间；
- `chunks`：顺序、检索文本、token 数、章节、页码范围、内容哈希、`tsvector` 和向量；
- `chunk_element_links`：Chunk 到 Element 的稳定映射和顺序；
- `conversations`：Project、owner、标题、`scope_mode` 和时间。`scope_mode` 只有两个值：`project` / `selected_papers`；单篇 Paper 问答就是 `selected_papers` 恰好一条，不单设 mode；Conversation 创建后 scope 不可修改，换范围即新建 Conversation（2026-08-20 定稿）；
- `conversation_scope_papers`：`selected_papers` 模式下保存创建时解析出的 `{paper_id, version_id}` 默认范围；
- `rag_answer` Run 输入保存本次解析后的版本范围快照 `[{paper_id, version_id}, ...]`（`project` 模式也在提交问题那一刻解析固化）；Conversation 的默认范围只影响新问题，不能替代 Run 快照；
- `messages`：Conversation 内严格递增顺序、角色、内容、状态和关联 Run；
- `claim_sets` / `claims`：回答的结构化 Claim；
- `evidence`：Project、Paper、PaperVersion、ParseRevision、Chunk、页码、章节和摘录；
- `citations`：Claim 与 Evidence 的关联；
- `model_invocations`：模型/profile、状态、token usage、延迟和错误分类，不保存完整 Prompt。

唯一约束至少保护：

- `(parse_revision_id, profile_hash)`；
- `(chunk_set_id, sequence)`；
- `(conversation_id, sequence)`；
- 一个 RAG Run 只提交一个最终 Assistant Message；
- 同一 Claim 不重复绑定同一 Evidence。

## Chunk 和索引策略

- Chunk 由一个或多个相邻 Element 组成，不修改原始 Element；
- 章节标题作为上下文前缀，表格和题注尽量保持完整；
- 每个 Chunk 保存 Element ID 列表和可回溯页码；
- Chunk/Profile 变化产生新 ChunkSet，旧索引保留直到无引用后再清理（清理属 Phase 4 GC 范畴）；
- Phase 2 只配置一个活动 Chunk/Embedding Profile；
- 首版使用 pgvector 精确检索，不创建 HNSW；
- token 计数使用 `tiktoken`（离线计算，2026-08-20 确认引入），tokenizer 名称参与 chunk profile hash；
- 新 ParseRevision 成功后创建独立 `indexing` Run：由 IngestionExecutor 的**结果提交事务**内同时创建 indexing Run + Outbox（沿用「状态 + Event + Outbox 原子」模式），保证解析成功必然跟随索引，不引入独立扫描循环（2026-08-20 定稿）；已有相同 ready ChunkSet 时 indexing Run 直接走复用路径；
- ChunkSet 属于 ParseRevision 而非 Project，同一论文跨 Project 复用索引；Project 收录已有 Paper 时不重复切分或 Embedding。

具体 Chunk 长度、Overlap、Embedding 模型和向量维度在对应切片的小实验中确定，并记录到本 Spec，不提前固定。

## Hybrid Retrieval

```text
原始问题
  ├─ pgvector semantic Top-K
  └─ PostgreSQL FTS Top-K
          ↓
       RRF 合并
          ↓
 Project / selected PaperVersion 强过滤
          ↓
 每篇论文结果上限 + 总 Token Budget
          ↓
 Evidence Candidates
```

首版不做 LLM Query Expansion、不做独立 reranker。FTS 使用 PostgreSQL `tsvector`，语言配置 `english`（语料为英文学术论文；中文支持不在本阶段范围，2026-08-20 定稿）；向量使用 pgvector cosine 距离精确检索；合并使用 RRF（`k=60`）。各路 Top-K（起始候选 20）、每篇论文结果上限和总 Token Budget 在检索切片的小实验中确定并记录于此。

Retriever 必须先限制 `owner_id`、`project_id`、`ProjectPaper`、`selected_version_id` 和 ready ChunkSet，再进行排序，不能在检索后仅靠应用层删除越权结果。

RetrievalResult 至少返回 Chunk、Paper/Version、章节/页码、各路分数和最终排序。分数和文本不进入普通日志；Event 只记录候选数量、使用的 profile 和耗时摘要。

## RAG Answer Run

RAG 使用后台 Run/Worker，复用 Phase 1 的 Outbox、Attempt、取消、重试和 SSE：

```text
POST Message
  → User Message + rag_answer Run + Event + Outbox 原子提交
  → Worker 认领 Run
  → Retrieval
  → 固化 Evidence
  → ChatModel 生成结构化 Claims + Evidence IDs
  → Citation Validator
  → Assistant Message / Claim / Citation + Run 终态原子提交
```

首版一个 Conversation 同时只允许一个未完成的回答 Run，避免回答顺序竞争。用户取消后保留 User Message 和 Run 历史，不创建成功的 Assistant Message。

模型结构化输出方向：

```text
answer_status: answered | insufficient_evidence
claims:
  - text
    evidence_ids[]
```

Claim 是**段落级**的：每条 Claim 对应回答中的一个段落级论述。首版采用严格策略——`answered` 状态下**每个 Claim 必须至少绑定一个 Evidence**，不引入「重要 Claim」的主观判定；零引用 Claim 直接判非法，触发一次结构修复重试，仍失败则 Run 稳定 FAILED（2026-08-20 定稿）。

无可用证据属于成功的 `insufficient_evidence` 业务结果，不是系统失败。

## Citation Validator

运行时确定性校验：

- Evidence 存在且属于当前 owner/Project；
- Evidence 的 Paper 属于该 Run 固化的版本范围快照（历史回答不受后续移出、换版或归档影响）；
- PaperVersion、ParseRevision、Chunk 和来源定位链完整；
- 模型只能使用本次 Run 提供的 Evidence ID；
- `answered` 状态下每个段落级 Claim 至少绑定一个 Evidence（严格策略，无例外）；
- 重复、缺失或伪造 Evidence ID 被拒绝；
- 未验证的模型生成 DOI、作者和年份不写入正式书目字段。

“Evidence 是否在语义上真正支持 Claim”由固定人工样本和可选评测完成，不声称确定性 Validator 可以完全判断语义正确性。

## API 方向

```text
POST /api/v1/projects/{project_id}/conversations
GET  /api/v1/projects/{project_id}/conversations
GET  /api/v1/conversations/{conversation_id}
GET  /api/v1/conversations/{conversation_id}/messages
POST /api/v1/conversations/{conversation_id}/messages

GET  /api/v1/projects/{project_id}/evidence/{evidence_id}
GET  /api/v1/projects/{project_id}/paper-versions/{version_id}/index-status
```

提交问题使用 `Idempotency-Key`，返回：

```text
202 Accepted
{
  "user_message_id": "...",
  "run_id": "...",
  "status": "queued"
}
```

Run 查询、取消和 Event/SSE 继续复用 `/api/v1/runs/{run_id}` 相关接口。

创建 Conversation 时可传 `scope_mode=project|selected_papers` 和 Project 内可见的 `paper_ids`；Paper 页面和多选入口调用同一端点。个人文献库中的 Paper 必须先选择/加入 Project，不提供独立 owner-scoped Chat API。

## Event 方向

新增小型事件：

- `indexing_started`、`chunking_completed`、`embedding_completed`、`indexing_completed`；
- `retrieval_started`、`retrieval_completed`；
- `model_generation_started`、`model_generation_completed`；
- `citation_validation_completed`；
- `answer_committed`、`model_usage_recorded`。

Event 不保存完整问题、Prompt、Chunk 文本、Evidence 摘录或最终回答；客户端收到 `answer_committed` 后重新查询 Message。

## 失败、重试和取消

- Provider 429、5xx 和网络超时属于临时错误，由 Provider Adapter 层进行最多 2 次短重试，耗尽后交给 Run 层按预算 RETRY_WAIT（同一错误只有一层主导重试之外的有限补充，2026-08-20 定稿）；
- 非法模型结构输出最多修复一次，仍失败则稳定 FAILED；
- Context 超限时按预算缩减一次，不循环压缩；
- 没有 ready ChunkSet 返回稳定 `project_not_indexed` 或部分未就绪提示；
- 无相关 Evidence 返回 `insufficient_evidence`；
- 重复 Job 不重复创建 ChunkSet、Message、Claim 或 Citation；
- Worker 在 Embedding 批次、Retrieval 后和模型调用前后检查取消；
- 外部模型调用不发生在数据库事务内；
- 最终 Message、Claim、Citation、Run 终态和 Event 在同一短事务提交。

## 安全和隐私

- 所有检索路径必须同时限制 owner 和 Project；
- 相同论文可以跨同一 owner 的 Project 复用索引，但 Evidence 仍是 Project-scoped；
- Provider Key 只来自服务端配置；
- 日志、Event 和 Trace 不记录完整论文、问题、Prompt、Evidence Context 或回答；
- 模型看到的上下文只包含本次 Run 选中的 Evidence；
- Paper 被移出 Project 后不参与新检索，历史 Message 的引用通过历史 Evidence 保持可读。

## 实现切片顺序

每个切片遵循「确认契约与不变量 → 失败测试 → 最小实现 → 重构 → 验证 → 更新进度」。

1. **资源管理边界**（已完成 2026-08-20，契约见下文「切片 1」）：Project 改名/归档/恢复、Paper 归档/恢复、active 过滤、归档对新 Run/收录的限制；归档 Paper 后同哈希上传仍复用已有 canonical Version，不自动恢复归档；归档 Project 存在非终态 Run 时返回 409；
2. **阶段契约与评测 Fixture**（已完成 2026-08-20，契约见下文「切片 2」）：定最小错误码（`project_not_indexed`、`conversation_busy`、`invalid_scope` 等）；评测语料使用合成 PDF，由子智能体在本切片构建，保证期望 Evidence 的页码/章节完全确定；
3. **Model Gateway**（已完成 2026-08-20，契约见下文「切片 3」）：`EmbeddingModel`/`ChatModel` Port、Fake Provider、OpenAI-compatible Adapter（基于已有 httpx2，不引入 SDK）、错误分类、`model_invocations` 表（不存完整 Prompt）；`pgvector` 与 `tiktoken` 依赖各自独立 `chore:` 提交；
4. **ChunkSet + Worker 分发**：结构感知 Chunk Builder（章节前缀、表格/题注完整、Element 映射）、profile 哈希、迁移；同切片落地 `RunType` 枚举与 Worker 按 `run_type` 显式分发的组合 Executor（indexing 执行器先只跑到 chunking，不带向量）；
5. **Indexing Run**：pgvector 镜像与迁移（compose/testcontainers 换 `pgvector/pgvector:pg18`，本地开发库可重建）、批量 Embedding、复用、重试和取消、`index-status` API；
6. **Hybrid Retrieval**：FTS（english）、向量检索、RRF、Project 强过滤和上下文预算；
7. **Evidence/Citation**：Evidence、Claim、Citation 和确定性 Citation Validator（段落级 Claim 严格绑定）；
8. **RAG Conversation**：Conversation、Message、版本范围快照、后台回答 Run 和最终原子提交、无证据路径；
9. **API 与最小 Web UI**：Chat 三入口、Run 进度（前端 `KNOWN_EVENT_TYPES` 扩充）、引用详情和 PDF 页码跳转；
10. **验收复盘**：评测实跑报告（Fake Provider 验证管线 + 真实 Provider 显式启用）、故障测试和学习笔记。

### 切片 1：资源管理边界（契约定稿）

已于 2026-08-20 实现完成。遵循 `../decisions/0002-archive-and-project-scoped-entrypoints.md`：归档优先，永久删除延后到 Phase 4。

#### 目标

为 Project 和 Paper 提供修改、归档与恢复能力，并把「归档资源只读」边界落到所有既有写入口，为后续 RAG/Workflow 保留完整历史可追溯性。

#### 范围

- **包含**：`projects`/`papers` 的 `archived_at` 列与迁移；Project 改名/归档/恢复 API；Paper 归档/恢复 API；列表 `include_archived` 过滤；归档对上传、收录、移除收录的 409 限制；同哈希复用已归档 Paper 的提示字段。
- **不包含**：前端 UI 入口（归档按钮、归档徽标，延后到切片 9 的 Chat UI 一并提供）；永久删除；归档对 Phase 2 之后新写入口（Conversation、Review Run）的限制，随对应切片落地。

#### 数据模型

- `projects` 增加 `archived_at`（timestamptz，可空，默认 null）；归档状态由该列派生，不加独立 status 列；
- `papers` 增加 `archived_at`（同上）；`paper_versions`、`project_papers` 不动；
- 迁移 `b3f5a8c1d9e2`，`down_revision = c84f2d7a91e6`；
- `Run` 领域新增 `ACTIVE_RUN_STATUSES`（QUEUED/RUNNING/RETRY_WAIT/CANCEL_REQUESTED），归档前检查以此为唯一事实来源。

#### API 契约

- `PATCH /api/v1/projects/{project_id}`：body `{name?, description?}`，至少一个字段（否则 422）；name 沿用创建校验（非空、≤200，违反 422）；已归档 → 409 `project_archived`；成功 200 返回 Project；
- `POST /api/v1/projects/{project_id}/archive`：幂等（已归档返回 200）；存在非终态 Run → 409 `project_has_active_runs`；成功 200 返回 Project；
- `POST /api/v1/projects/{project_id}/restore`：幂等，200；
- `GET /api/v1/projects` 增加 `include_archived` 查询参数（默认 false，只返回 active）；
- `POST /api/v1/library/papers/{paper_id}/archive` / `restore`：幂等 200，返回 `{paper_id, archived_at}`；不存在/越权 404；
- `GET /api/v1/library/papers` 增加 `include_archived`（默认 false）；列表条目增加 `archived_at`；
- 已归档 Project 上的写操作一律 409 `project_archived`：`POST /paper-files` 上传、`POST /papers` 收录、`DELETE /papers/{paper_id}` 移除；读接口（GET 详情、Run/Event 查询、document/elements/file）保持可用；
- 收录已归档 Paper 到 Project → 409 `paper_archived`；
- 上传同 SHA-256 命中已归档 Paper 的 canonical Version：正常复用，不自动恢复归档，`UploadResult` 响应增加 `paper_archived: true` 提示（幂等重放按当前 Paper 状态实时计算）；新文件上传不受影响；
- Project 响应模型增加 `archived_at`。

#### 关键不变量

1. 所有查询维持 owner 隔离，越权/不存在统一 404；
2. 归档/恢复幂等，重复调用不刷新归档时间；
3. 归档只冻结写操作，不产生 Run/Event，不破坏已有 ProjectPaper 与历史数据；
4. Project 归档/恢复/改名写 `updated_at`；Paper 无 `updated_at`，不新增；
5. 归档 Project 存在非终态 Run 时拒绝归档，用户需先等待或取消；
6. 幂等键重放不是新写操作：命中已存响应直接返回，不受归档 409 限制。

#### 测试要点

- Domain：Project/Paper 归档、恢复幂等、`update_details` 校验；
- Application：授权 404、归档幂等、非终态 Run 409、收录/移除/上传的归档 409、复用已归档 Paper 提示且不恢复；
- API：全部端点契约与稳定业务码（`project_archived`/`project_has_active_runs`/`paper_archived`）；
- PostgreSQL：`list_by_owner` 归档过滤、`update` 持久化、`has_active_runs` 状态集合；迁移 upgrade/downgrade 在一次性容器中实跑通过。

### 切片 2：阶段契约与评测 Fixture（契约定稿）

已于 2026-08-20 定稿。本切片不修改生产代码，只确定后续切片必须遵守的最小错误码，并交付确定性评测资产。

#### 最小错误码

- **404**：`conversation_not_found`、`evidence_not_found`（沿用「越权/不存在统一 404」原则）；
- **409**：`conversation_busy`（一个 Conversation 已有未完成回答 Run）、`project_not_indexed`（提问时范围内没有任何 ready ChunkSet，快速失败；部分就绪不阻塞，检索只覆盖 ready 范围）、`project_archived` / `paper_archived` / `project_has_active_runs`（切片 1 已有）；
- **422**：`invalid_scope`（scope_mode 非法、selected_papers 为空或含未收录/已归档/其他 owner 的 Paper）；
- 模型/基础设施失败不暴露为 API 错误码，走 Run FAILED + 错误分类（沿用 Phase 1）。

#### 评测资产

位于 `backend/tests/evaluation/`：

- `corpus/`：4 篇完全合成的英文学术风格 PDF（`gnn-survey`、`positional-encoding`、`gnn-molecular`、`rl-robotics`，各 4 页，含标题/摘要/编号章节/段落，`positional-encoding` 与 `gnn-molecular` 含 Courier 排版表格），由 `generate.py` 确定性生成（复用 Phase 1 手写最小 PDF 机制，不引入新依赖），PDF 提交进仓库；每篇植入独有的事实性陈述（虚构术语与数值），答案来源在语料中唯一确定；
- `manifest.json`：固定问题集 14 题，`corpus` 段把稳定语料 ID 映射到 PDF 文件、页数和植入事实（关键词 + 页码 + 章节），`questions` 段字段为 `id` / `category` / `question` / `scope`（`project` 或 `selected_papers`）/ `expected`（`answer_status` + `must_cite[{paper, pages, sections}]`）/ `notes`；
- `README.md`：语料设计、问题分类与评测运行方式说明；
- 一致性校验 `tests/infrastructure/test_evaluation_fixtures.py`：只用 pypdf，断言页数与植入关键词/章节出现在 manifest 声明页码，不调用 Docling 与模型。

四类问题覆盖：单篇事实型（期望引用特定 paper + 页码/章节）5 题、跨篇综合型（must_cite 恰好两篇，`gnn-survey` 与 `gnn-molecular` 主题相近）3 题、明确无答案型（期望 `insufficient_evidence`）3 题、范围边界型（selected_papers 排除答案所在 paper，期望 `insufficient_evidence`）3 题。

评测指标（切片 10 实跑，只报告实跑结果，不使用虚构质量指标）：Retrieval Recall@K（期望 paper/页面的 Chunk 是否进入 Top-K）、Citation validity（是否通过确定性 Validator）、Citation completeness（must_cite 覆盖率）、少量人工 Groundedness；无答案/范围边界题以 `answer_status == insufficient_evidence` 为通过。

#### 语料语言决定

评测语料为英文合成 PDF（2026-08-20 定稿）：检索语料是英文论文，FTS 使用 PostgreSQL `english` 配置，评测必须同语言，避免跨语言分词干扰使评测结论失真。

### 切片 3：Model Gateway（契约定稿）

已于 2026-08-20 实现完成。

#### Port 签名

- `EmbeddingModel.embed(texts: list[str]) -> EmbeddingResult`（vectors + usage + model 名）；空列表直接返回空结果，不发起请求；Port 暴露 `provider`/`model` 属性供调用记录使用；
- `ChatModel.generate(messages: list[ChatMessage], *, json_schema: dict | None = None, max_tokens: int | None = None) -> ChatResult`（原始 content 字符串 + usage prompt/completion tokens + model 名）；
- 结构化输出只表达意图：Adapter 内把 `json_schema` 映射为 OpenAI `response_format`（`json_schema` 优先，构造参数 `json_schema_supported=False` 时降级 `json_object`）；JSON 解析与业务 Schema 校验留给切片 8；
- `ModelInvocationRepository`：`add` + `list_by_run`。

#### 错误分类

`domain/model_errors.py`，接入现有 `is_permanent_error`：

| 错误 | 分类 | 触发 |
|---|---|---|
| `ModelRateLimitError` | 临时 | HTTP 429，Adapter 短重试耗尽后 |
| `ModelServerError` | 临时 | HTTP 5xx、网络连接失败 |
| `ModelTimeoutError` | 临时 | 网络超时（客户端 timeout，默认 60s） |
| `ModelAuthError` | 永久 | HTTP 401/403、缺少 API Key |
| `ModelInvalidRequestError` | 永久 | HTTP 400 等其余 4xx |
| `ModelResponseError` | 永久 | 响应 JSON 畸形或缺约定字段；Adapter 不做结构修复重试 |

Adapter 层对临时错误最多重试 `AGENT_MODEL_MAX_RETRIES` 次（默认 2，固定退避 1s/2s），耗尽交 Run 层按预算 RETRY_WAIT；永久错误不重试。

#### model_invocations 表

`invocation_id`（PK）、`run_id`（可空 FK → runs，切片 5/8 接线时填）、`capability`（embedding/chat）、`provider`、`model`、`status`（succeeded/failed）、`prompt_tokens`/`completion_tokens`（可空）、`latency_ms`、`error_type`（可空）、`created_at`。**不存 Prompt/响应内容**。迁移 `d6e1f7a3b9c2`，`down_revision = b3f5a8c1d9e2`。

#### Settings 清单

扁平 `AGENT_` 前缀，延续 `from_env` 手写解析：`AGENT_EMBEDDING_BASE_URL` / `AGENT_EMBEDDING_API_KEY` / `AGENT_EMBEDDING_MODEL` / `AGENT_EMBEDDING_DIMENSIONS`、`AGENT_CHAT_BASE_URL` / `AGENT_CHAT_API_KEY` / `AGENT_CHAT_MODEL`、`AGENT_MODEL_TIMEOUT_SECONDS`（默认 60）、`AGENT_MODEL_MAX_RETRIES`（默认 2）。API Key 默认 None，缺失时 Adapter 首次调用抛 `ModelAuthError`（启动不崩溃，本地开发用 Fake）。

#### Provider 默认值与来源依据

- Embedding：智谱 `embedding-3`，OpenAI 兼容端点 `https://open.bigmodel.cn/api/paas/v4/embeddings`，默认维度 1024（可选 256/512/1024/2048，维度参与后续 embedding profile hash）；
- Chat：DeepSeek `deepseek-v4-flash`，OpenAI 兼容 ChatCompletions，base `https://api.deepseek.com`；
- 两者均为 2026-08-20 与用户定稿的默认配置；Adapter 是通用 OpenAI-compatible 实现，base_url/api_key/model 全部走 Settings，不写死 Provider。

#### ModelGateway

`application/model_gateway.py`：包装两个 Port，统一计时，调用后把 invocation 记录经 Repository 持久化（独立短事务；记录失败只记日志不影响调用结果）；模型调用不发生在数据库事务内。执行器接线（传 `run_id`）在切片 5/8。

#### 测试要点

- RESPX 契约（`tests/infrastructure/test_openai_compatible_models.py`，16 例）：成功形状与请求体、usage 解析、空批量不发请求、429 重试后成功、429/5xx/超时耗尽、401/400 永久不重试、JSON 畸形与缺字段、`json_schema`/`json_object` response_format；
- Gateway（`tests/application/test_model_gateway.py`，5 例）：成功/失败记录、error_type 分类、记录失败不影响结果、run_id 可空；
- PostgreSQL 集成（`tests/integration/test_model_invocation_repository.py`，3 例）：字段往返、run_id 可空、空查询；
- 真实 Provider 冒烟 `AGENT_RUN_PROVIDER_TESTS=1` 显式启用（仿 `AGENT_RUN_DOCLING_TESTS`），默认跳过。

## 测试方式

- **Domain**：Chunk/Profile 哈希、Claim/Evidence 关系和 Citation Validator；
- **Application**：索引复用、RAG 编排、无证据、重复 Job、取消和最终原子提交；
- **PostgreSQL**：pgvector/FTS、唯一约束、Project 强过滤和并发提交；
- **API/SSE**：Project/Paper 归档限制、整个 Project/单篇/多篇 Conversation、幂等提问、刷新恢复、取消和引用跳转；
- **Provider Contract**：Fake Embedding/Fake Chat 为默认，真实 Provider 显式启用；
- **Evaluation**：固定 Project、问题、期望 Paper/Evidence 和明确无答案问题。

普通测试不得访问真实模型。评测至少记录 Retrieval Recall@K、Citation validity/completeness 和少量人工 Groundedness 结果；不使用虚构质量指标。

## 阶段完成条件

- ParseRevision 可以生成版本化、可复用的 ready ChunkSet；
- 同一 Paper 跨 Project 不重复解析、Chunking 或 Embedding；
- Hybrid Retrieval 只返回当前 Project 可见文献；
- Project、单篇 Paper 和多篇 Paper 三种入口共用 Project-scoped Retrieval，Run 保存不可变 Version 范围快照；
- 回答只能引用本次 Run 的有效 Evidence；
- 无证据问题明确返回证据不足；
- Conversation、Message、Run 和 Citation 刷新后可恢复；
- 引用可以跳转到 PaperVersion、Element 和 PDF 页码；
- Provider 临时错误、重复 Job、取消和 SSE 重连有自动测试；
- 有固定 Retrieval/Citation 评测数据和真实运行报告；
- 阶段 Spec、模块学习笔记和已知限制已更新。

## 实现前需要确定

以下事项已于 2026-08-20 定稿：Provider 方案（OpenAI-compatible Adapter + Fake）、pgvector 镜像替换（本地库可重建）、`tiktoken` 引入、scope 模型（两值、不可改）、段落级 Claim 严格绑定、indexing Run 创建时机、Worker run_type 分发、评测语料用合成 PDF。详见「已确定事项」。

以下参数仍在对应切片的小实验中确定，不阻塞当前阶段边界：

1. Chunk 长度、Overlap 和表格处理规则（切片 4）；
2. 首个 Embedding/Chat Model 及向量维度（切片 3/5）；
3. semantic/FTS Top-K、RRF 之外的每篇论文上限和总 Token Budget（切片 6）；
4. Context Token Budget 和结构化输出 Schema 细节（切片 7/8）；
5. 小型评测集的具体合成论文与问题清单（切片 2，子智能体构建）。

## 已确定事项

- 2026-08-20：承认 `DocumentContentReader` 未作为独立 Port 落地，现状为应用服务 `DocumentQueryService`（授权读路径）+ Worker 内直接使用 `ElementRepository`；更新本 Spec 与 Phase 1 Spec，不新建 Port；
- 2026-08-20：Worker 新增按 `run_type` 显式分发的组合 Executor，领域层引入 `RunType` 枚举，未知类型显式失败；
- 2026-08-20：indexing Run 由 IngestionExecutor 结果提交事务内随解析成功原子创建（Run + Outbox 同事务），不引入独立扫描循环；ChunkSet 属于 ParseRevision，跨 Project 复用；
- 2026-08-20：Provider 方案为 `EmbeddingModel`/`ChatModel` 窄 Port + 基于 httpx2 的 OpenAI-compatible Adapter + 确定性 Fake；不引入 openai/LangChain SDK；Provider Key 只来自服务端配置；Adapter 层对 429/5xx/超时最多 2 次短重试，耗尽交 Run 层；
- 2026-08-20：新增依赖 `pgvector`（SQLAlchemy 绑定）与 `tiktoken`，各自独立 `chore:` 提交；compose 与 Testcontainers 镜像从 `postgres:18` 换为 `pgvector/pgvector:pg18`，本地开发库允许重建，迁移负责 `CREATE EXTENSION vector`；
- 2026-08-20：`scope_mode` 只有 `project` / `selected_papers` 两值，单篇即一条的 `selected_papers`；Conversation 创建后 scope 不可改；Run `input_payload` 固化 `[{paper_id, version_id}, ...]` 快照；
- 2026-08-20：Citation 严格策略——Claim 为段落级，`answered` 状态下每个 Claim 必须至少绑定一个本次 Run 的 Evidence，否则修复重试一次后 FAILED；
- 2026-08-20：归档语义——归档 Paper 后同哈希上传仍复用已有 canonical Version，不自动恢复归档；归档 Project 存在非终态 Run 时归档返回 409；永久删除仍属 Phase 4；
- 2026-08-20：FTS 语言配置 `english`；RRF `k=60`；不做 Query Expansion 和独立 reranker；
- 2026-08-20：评测语料使用合成 PDF（切片 2 由子智能体构建），问题集覆盖单篇事实、跨篇综合、明确无答案和范围边界四类；指标只报告实跑的 Retrieval Recall@K、Citation validity/completeness 和少量人工 Groundedness。

## 预期学习笔记

- `docs/learning-journal/modules/model-gateway.md`；
- `docs/learning-journal/modules/hybrid-retrieval-and-pgvector.md`；
- `docs/learning-journal/modules/evidence-and-citation-integrity.md`；
- `docs/learning-journal/modules/rag-conversation.md`。
