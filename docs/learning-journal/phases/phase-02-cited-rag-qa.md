# Phase 2：有引用的 RAG 文献问答

## 状态

已完成（2026-08-21）。实施前方案已于 2026-08-20 与用户逐项讨论确认，结论见文末「已确定事项」。切片 10 已完成固定评测、故障证据审计、Phase 2 Playwright E2E、真实 Docling/Embedding/Chat Smoke 与文档收口；Fake 与真实 Provider 结论严格分开。

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
- Chunk/Profile 变化产生新 ChunkSet，旧索引保留直到无引用后再清理；ADR-0004 已把自动 GC 推迟到
  Demo-ready Core v1 之后；
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

首版不做 LLM Query Expansion、不做独立 reranker。FTS 使用 PostgreSQL `tsvector`，语言配置 `english`（语料为英文学术论文；中文支持不在本阶段范围，2026-08-20 定稿）；向量使用 pgvector cosine 距离精确检索；合并使用 RRF（`k=60`）。各路 Top-K、每篇论文结果上限和总 Token Budget 已经切片 6 小实验校准（2026-08-21）：`top_k=20`、`per_paper_limit=8`、`token_budget=3000`，实验过程见下文「切片 6」小节。

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
4. **ChunkSet + Worker 分发**（已完成 2026-08-20，契约见下文「切片 4」）：结构感知 Chunk Builder（章节前缀、表格/题注完整、Element 映射）、profile 哈希、迁移；同切片落地 `RunType` 枚举与 Worker 按 `run_type` 显式分发的组合 Executor（indexing 执行器先只跑到 chunking，不带向量）；
5. **Indexing Run**（已完成 2026-08-21，契约见下文「切片 5」）：pgvector 镜像与迁移（compose/testcontainers 换 `pgvector/pgvector:pg18`，本地开发库可重建）、批量 Embedding、复用、重试和取消、`index-status` API；
6. **Hybrid Retrieval**（已完成 2026-08-21，契约见下文「切片 6」）：FTS（english）、向量检索、RRF、Project 强过滤和上下文预算；
7. **Evidence/Citation**（已完成 2026-08-21，契约见下文「切片 7」）：Evidence、Claim、Citation 和确定性 Citation Validator（段落级 Claim 严格绑定）；
8. **RAG Conversation**（已完成 2026-08-21，契约见下文「切片 8」）：Conversation、Message、版本范围快照、后台回答 Run 和最终原子提交、无证据路径；
9. **API 与最小 Web UI**（已完成 2026-08-21，契约见下文「切片 9」）：Chat 三入口、Run 进度（前端 `KNOWN_EVENT_TYPES` 扩充）、引用详情和 PDF 页码跳转；
10. **验收复盘**（已完成 2026-08-21，契约见下文「切片 10」）：评测实跑报告（Fake Provider 验证管线 + 真实 Provider 显式启用）、故障证据审计、Phase 2 E2E 和学习笔记。

### 切片 1：资源管理边界（契约定稿）

已于 2026-08-20 实现完成。遵循 `../decisions/0002-archive-and-project-scoped-entrypoints.md`：归档
优先；永久删除原定 Phase 4，后由 ADR-0004 调整为不属于 Demo-ready Core v1。

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

评测指标（切片 10 实跑，只报告实跑结果，不使用虚构质量指标）：Retrieval Recall@K（期望 paper/页面的 Chunk 是否进入 Top-K）、Citation validity（是否通过确定性 Validator）、Citation completeness（must_cite 覆盖率）；无答案/范围边界题以 `answer_status == insufficient_evidence` 为通过。Groundedness 只有实际人工核对后才可报告，本阶段未运行该指标。

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

### 切片 4：ChunkSet 与 Worker 分发（契约定稿）

已于 2026-08-20 实现完成。本切片只做到 chunking（结构化 Chunk 落库），Embedding/向量在切片 5。

#### RunType 与 Worker 分发

- `domain/run.py` 新增 `RunType` StrEnum：`INGESTION` / `INDEXING` / `RAG_ANSWER`（三个都定义，`RAG_ANSWER` 切片 8 才接线）；`Run.run_type` 保持 `str` 注解（DB 列与历史调用不变），但 `create_run` 参数收窄为 `RunType | str` 并在创建时校验枚举取值，非法值直接 `ValueError`；
- `application/run_dispatcher.py` 新增 `RunDispatcher` 组合执行器：按 `run.run_type` 分发到已注册执行器；未知类型或未接线类型把 Run 推进 FAILED（`run_failed` 事件，错误类型 `unknown_run_type`），不静默执行；`RunExecutionService` 的单 executor 签名不变，dispatcher 作为组合 executor 注入；
- `IngestionExecutor`/`IndexingExecutor` 各自增加 run_type 防御：收到不匹配类型直接抛 `ValueError`（dispatcher 已兜底，双保险）。

#### ChunkProfile

`domain/chunk_profile.py`，冻结 dataclass：

| 字段 | 默认值 | 来源 |
|---|---|---|
| `max_tokens` | 512 | `AGENT_CHUNK_MAX_TOKENS` |
| `overlap_tokens` | 64 | `AGENT_CHUNK_OVERLAP_TOKENS` |
| `tokenizer` | `cl100k_base` | 固定（tiktoken） |
| `include_section_prefix` | true | 固定 |
| `embedding_provider` | — | 切片 3 `AGENT_EMBEDDING_BASE_URL` 的主机名（Settings 无独立 provider 字段，base_url 变化即 Provider 变化） |
| `embedding_model` | — | `AGENT_EMBEDDING_MODEL` |
| `embedding_dimensions` | — | `AGENT_EMBEDDING_DIMENSIONS` |

- 512/64 是实验起点，切片 6 检索实验可校准；
- `profile_hash`：规范化 JSON（sort_keys + 紧凑分隔符）的 sha256，模式照搬 `parser_profile_hash`；chunk 与 embedding 参数共同参与一个 hash——一个 ChunkSet 同时固定两者；
- 参数校验：`max_tokens > 0`、`0 <= overlap_tokens < max_tokens`、tokenizer 非空。

#### 数据模型

迁移 `e9c4d2f8a1b7`（`down_revision = d6e1f7a3b9c2`），upgrade/downgrade 已在一次性容器中实跑通过：

- `chunk_sets`：`chunk_set_id`（PK）、`parse_revision_id`（FK → document_parse_revisions）、`profile_hash`、`config`（JSONB）、`status`（`running`/`ready`/`failed`）、`error`（JSONB 可空）、`created_at`、`completed_at`；唯一约束 `(parse_revision_id, profile_hash)`（Effectively Once，与 ParseRevision 同构）；
- `chunks`：`chunk_id`（PK）、`chunk_set_id`（FK）、`sequence`、`text`、`token_count`、`section_path`（可空）、`page_start`/`page_end`（可空）、`content_hash`；唯一约束 `(chunk_set_id, sequence)`。**本切片不建 `search_vector`/`embedding` 列**（切片 5 迁移再加，避免二次迁移 chunk 表）；
- `chunk_element_links`：`chunk_id`（FK）+ `element_id`（FK）+ `sequence`；复合主键 `(chunk_id, element_id)`（同一 Chunk 不重复绑定同一 Element），`element_id` 上另有普通索引支持反查某 Element 属于哪些 Chunk；Chunk 内 Element 顺序由 `sequence` 表达。

#### ChunkBuilder 规则

`domain/chunk_builder.py`，纯函数 `build_chunks(elements, locations, profile) -> list[ChunkDraft]`（草稿不含持久化 ID，执行器提交时分配，保持确定性可测）：

- 组合相邻文本类 Element，目标 `max_tokens`（预算按单元 token 和估算，最终 `token_count` 对拼接后的完整文本精确计数）；
- 相邻 Chunk 重叠 `overlap_tokens`：按整 Element 回带，不切半个 Element；最后一个单元单独超过 overlap 预算时该处不重叠；
- 单个超过 `max_tokens` 的 Element（如大表格）允许独立成 Chunk 超限存在，不硬切；
- caption 子 Element 并入紧邻前一个单元中的 table/figure 父 Element，保证表格与题注同 Chunk；表格无 `text` 时把 payload 单元格渲染为纯文本行（` | ` 分隔）参与切分；
- 章节标题（`section_heading`）不单独成 Chunk：是天然 Chunk 边界（遇到标题先关闭当前 Chunk），并作为后续 Chunk 的上下文前缀（`include_section_prefix` 时以 `标题\n\n正文` 拼入 text 开头，前缀计入 token_count）；章节边界不做重叠回带；
- `page_header`/`page_footer` 不进入 Chunk；text 为空（含纯空白）的 Element（如未抽取的 figure）不成 Chunk；
- `page_start`/`page_end` 取 Chunk 内 Element 来源定位的最小/最大页码，无定位为 null；
- `content_hash` 为最终 text 的 sha256；token 计数用 tiktoken `cl100k_base`（进程内缓存编码）。

#### IndexingExecutor 与触发

- `application/indexing_executor.py`，结构与 IngestionExecutor 同构：事务 A 准备（按 `(parse_revision_id, profile_hash)` 查 ChunkSet：ready → 复用直接 SUCCEEDED + `indexing_completed(reused=true)`；failed/running 遗留行重置复用同一行；无 → 创建 running 行 + `indexing_started` 事件）→ 短事务分页读 Element + 定位 → 事务外 ChunkBuilder 构建 → 事务 C 原子提交（chunks + links + ChunkSet ready + Run SUCCEEDED + `chunking_completed` + `indexing_completed`）；取消检查点在事务 A/C 入口；
- 错误分类：Parse Revision 不存在或尚未成功属永久输入错误（新增 `IndexingInputError`，已注册进 `is_permanent_error`），Run 直接 FAILED 且不创建 ChunkSet；revision 已成功但零 Element 属合法（空文档），产生空 ChunkSet 并 ready；ChunkBuilder 其他未知异常走临时错误——ChunkSet FAILED + Run RETRY_WAIT + Outbox 重置；
- indexing Run 的 `input_payload`：`{"parse_revision_id": ..., "version_id": ...}`（version_id 冗余用于事件与排查）；`project_id`/`owner_id` 与触发它的 ingestion Run 相同；
- **触发时机**：IngestionExecutor 结果提交事务内（含复用已有 Revision 的提前返回路径）同时创建 indexing Run + `run_created` 事件 + QueueOutbox（同一事务，沿用「状态 + Event + Outbox 原子」不变量），不引入独立扫描循环；
- Worker 装配：`RunDispatcher(executors={INGESTION: ..., INDEXING: ...})` 注入 `RunExecutionService`；IndexingExecutor 本切片不接 EmbeddingModel。

#### 事件

新增 `indexing_started`（chunk_set_id、profile_hash）、`chunking_completed`（chunk_set_id、chunk_count、profile_hash）、`indexing_completed`（chunk_set_id、chunk_count、reused）。payload 不含 Chunk 文本。indexing Run 终态事件用 `indexing_completed` 而非 `result_committed`。

#### 测试要点

- Domain：`test_chunk_profile.py`（5 例：默认值、哈希确定性、chunk/embedding 参数均参与哈希、非法参数）；`test_chunk_builder.py`（10 例：分组、整 Element 重叠、超限独立 Chunk、表格题注同 Chunk、章节前缀与开关、章节边界、页眉页脚/空文本排除、页码范围、空文档、content_hash）；
- Application：`test_indexing_executor.py`（9 例：全链路事件序列、ready 复用、failed 行重置重跑、空文档、revision 缺失/未成功永久 FAILED、构建失败 RETRY_WAIT、取消竞争、run_type 防御）；`test_run_dispatcher.py`（3 例：分发到注册执行器、未知类型 FAILED、未接线枚举类型 FAILED）；`test_ingestion_executor.py` 新增 3 例（成功后同事务产生 indexing Run + run_created + Outbox、复用路径同样触发、run_type 防御）；
- Integration：`test_chunk_repository.py`（4 例：ChunkSet 往返与唯一约束、状态保存、Chunk 往返与 sequence 唯一、links 复合主键与有序查询）；`test_queue_worker.py` 新增端到端（Outbox → ARQ → ingestion SUCCEEDED → 自动创建 indexing Run → 第二轮派发执行 → ChunkSet ready、Chunk/links 可查，Fake Parser）。

### 切片 5：Indexing Run（契约定稿）

已于 2026-08-21 实现完成。Indexing Run 在切片 4 的 chunking 之后接入批量 Embedding 与 pgvector/tsvector 检索列，并提供 `index-status` 查询 API。

#### 镜像与迁移

- `deploy/compose/compose.yml`、`deploy/compose/e2e.yml` 与 `tests/integration/conftest.py` 的 PostgreSQL 镜像统一换为 `pgvector/pgvector:pg18`；conftest 在 `create_all` 前执行 `CREATE EXTENSION IF NOT EXISTS vector`（与迁移顺序一致）。本地开发库数据卷需重建（`down -v` 后 `up`）；
- 迁移 `f2a7b3c9d4e1`（`down_revision = e9c4d2f8a1b7`，upgrade/downgrade 已在一次性容器中实跑通过）：`CREATE EXTENSION IF NOT EXISTS vector`；`chunks` 增加 `embedding`（`vector(1024)` 可空列，pgvector SQLAlchemy 类型 `Vector(1024)`）与 `search_vector`（`tsvector` 生成列，`GENERATED ALWAYS AS (to_tsvector('english', text)) STORED`，SQLAlchemy `Computed`，GIN 索引 `ix_chunks_search_vector`）；
- **维度固定取舍**：`embedding` 列维度在迁移时固定为 1024（与 `AGENT_EMBEDDING_DIMENSIONS` 默认值一致），改维度需要新迁移，这是有意取舍；向量列不建索引（首版精确检索，不建 HNSW，切片 6 检索实验后再评估）。

#### Embedding 阶段事务与重跑语义

IndexingExecutor 流程调整为：事务 A 准备（复用/创建/重置 ChunkSet + `indexing_started`）→ 短事务读 Element → 事务外 ChunkBuilder → 事务 C 提交 chunks/links（**ChunkSet 保持 `running`**，发 `chunking_completed`）→ Embedding 阶段 → 最终事务提交 ready 与 Run 终态：

- Embedding 阶段循环：短事务（持 Run 行锁检查取消 + 读 embedding 为 null 的一批，批次大小 `AGENT_EMBEDDING_BATCH_SIZE` 默认 32）→ 事务外经 ModelGateway 调 EmbeddingModel（每次调用记录 `model_invocations`，传 `run_id`）→ 短事务写回该批向量；
- 批次间取消：`CANCEL_REQUESTED` → Run `CANCELLED`，已写向量保留，ChunkSet 保持 `running`，无收尾事件；
- 最终事务：ChunkSet `ready` + Run `SUCCEEDED` + `embedding_completed`（`embedded_count`、`prompt_tokens` 摘要）+ `indexing_completed`；
- **重跑语义（Effectively Once）**：failed/running 遗留 ChunkSet 重置复用同一行；chunks 已存在（上次事务 C 已提交）则跳过 chunking，只补 embedding 为 null 的批次；`(chunk_set_id, sequence)` 唯一约束兜底重复提交，重复 Job 不产生重复 chunks；
- 错误分类沿用切片 3：临时错误（429/5xx/超时）→ ChunkSet `failed` + Run `RETRY_WAIT` + Outbox 重置；永久错误（auth/invalid_request/response）→ Run `FAILED`；
- 空 ChunkSet（零 chunks）直接 ready，不调用模型。

#### Embedding backend 开关与 fake profile 映射

- 新增 Settings：`AGENT_EMBEDDING_BACKEND`（`fake` / `openai_compatible`，**默认 `fake`**——本地开发与测试默认不触网，仿 `AGENT_PARSER_BACKEND` 模式）与 `AGENT_EMBEDDING_BATCH_SIZE`（默认 32）；
- `fake`：生产侧 `infrastructure/models/fake_models.py` 的确定性 `FakeEmbeddingModel`（文本 SHA-256 派生向量，维度固定 1024 与列一致），ChunkProfile 的 embedding 三元组固定为 `provider="fake", model="fake-embedding", dimensions=1024`，避免 fake 产出的 ChunkSet 与真实 profile 混淆；
- `openai_compatible`：切片 3 的 `OpenAiCompatibleEmbedding`（provider 取 `AGENT_EMBEDDING_BASE_URL` 主机名），profile 三元组来自 `AGENT_EMBEDDING_*`，缺 API Key 时首次调用抛 `ModelAuthError`；
- Worker 装配 `_build_model_stack` 统一产出 `ModelGateway + ChunkProfile + 可关闭 Adapter`，`ModelGateway` 的 invocation 记录持久化走 `SqlalchemyModelInvocationRepository`。

#### index-status API

`GET /api/v1/projects/{project_id}/paper-versions/{version_id}/index-status`（在 `documents.py`，授权链与 document/elements 一致：owner → Project → ProjectPaper → selected Version，越权/不存在 404；无当前 Revision → 404 `document_not_ready`）：

```text
200 {
  "revision_id": "...",
  "chunk_set": {"chunk_set_id", "status", "chunk_count", "embedded_count", "profile_hash"} | null,
  "indexing_run_id": "..." | null
}
```

`chunk_set` 取当前 Revision 最新创建的 ChunkSet（`ChunkSetRepository.get_latest_by_revision`），无 ChunkSet 为 null；`indexing_run_id` 取该 Revision 最近一次 indexing Run（`RunRepository.get_latest_indexing_run_id`，按 `input_payload.parse_revision_id` 匹配）。

#### 事件

完整索引事件序列：`indexing_started` → `chunking_completed`（chunk_set_id、chunk_count、profile_hash）→ `embedding_completed`（chunk_set_id、embedded_count、prompt_tokens）→ `indexing_completed`（chunk_set_id、chunk_count、reused）。复用路径仍只有 `indexing_completed(reused=true)`。重跑跳过 chunking 时无 `chunking_completed`。

#### 测试要点

- Application（`test_indexing_executor.py`，14 例）：全链路事件序列（含 `embedding_completed`）与 1024 维向量写回、多批（batch=2 时 5 chunks 分 3 批）、批次间取消保留已写向量、模型临时错误 RETRY_WAIT（chunks 保留、invocation 记录 failed 含 run_id）、永久错误 FAILED、部分失败后重跑只补 null 且不重复 chunks、空 ChunkSet 不调模型、ready 复用不调模型；
- Integration：`test_chunk_repository.py` 新增 3 例（向量往返与 pending/count_embedded、cosine 距离 `<=>` Top-K 排序、tsvector 生成列 `@@ plainto_tsquery` 命中/不命中与词干化）；迁移 upgrade/downgrade 在一次性容器实跑通过；
- Worker 端到端（`test_queue_worker.py`）：ingestion → indexing 两轮派发后 chunks 带 1024 维 embedding 与非空 search_vector、ChunkSet ready、`model_invocations` 含 indexing run_id、`get_index_status` 授权链走通且计数正确（Fake Parser + 生产侧 Fake Embedding）；
- API（`test_index_status.py`，7 例）：ready/running/failed/无 ChunkSet（null）/未知版本 404/越权 404/无当前 Revision 404 `document_not_ready`。

#### 已知限制

- 向量维度固定 1024（迁移级取舍），更换维度需新迁移并重建索引；
- embedding 列无向量索引，精确检索随语料增长的性能在切片 6 检索实验中评估；
- fake backend 的 ChunkProfile 与真实 Provider 的 profile 不同哈希，切换 backend 会产生新 ChunkSet 并重新切分+Embedding（预期行为）；
- 取消后 ChunkSet 保持 `running`，由下一次触发（重跑）补齐，不提供独立「续跑」入口；
- `search_vector` 使用 `english` 配置，中文语料分词不在本阶段范围。

### 切片 6：Hybrid Retrieval（契约定稿）

已于 2026-08-21 实现完成。本切片不新增 API 与表结构，交付 Project-scoped 混合检索能力；run_id 接线在切片 8。

#### 结构

```text
Retriever（application/retriever.py）
  ├─ ModelGateway.embed([query])        # 查询向量，记录 model_invocations（run_id 可空）
  ├─ 一只读短事务内两路 SQL（ChunkRepository）：
  │    ├─ search_semantic：embedding <=> :query_vector 升序 Top-K
  │    └─ search_fulltext：search_vector @@ plainto_tsquery('english', :q) 按 ts_rank 降序 Top-K
  └─ domain/retrieval.py 纯函数：rrf_merge(k=60) → apply_per_paper_limit → apply_token_budget
```

- Domain 纯函数：`ScoredChunk`（单路有序候选）→ `RankedCandidate`（含 semantic_rank/fts_rank/rrf_score）；`rrf_score = Σ 1/(60 + rank)`，平局按 semantic rank → fts rank → chunk_id 稳定决胜；`apply_per_paper_limit` 保持原序每篇截断；`apply_token_budget` 按 chunk token_count 贪心累计（超限候选跳过、后续小候选仍可入选，预算 0 返回空）。
- Repository：`SqlalchemyChunkRepository` 新增 `search_semantic`/`search_fulltext`，返回 `RetrievedChunk(chunk, paper_id, version_id)`；两路共用 `_scoped_select` 强过滤链。
- Application `Retriever.retrieve(...)` 返回 `RetrievalResult` 列表：chunk（id/text/token_count/section_path/page_start/page_end）、paper_id、version_id、semantic_rank/fts_rank（未命中 None）、rrf_score、最终 rank（截断后从 1 重编号）。空查询（含纯空白）直接 `ValueError`，不调用模型也不访问数据库。

#### 强过滤链（SQL 内完成，两路一致）

```text
projects.owner_id = :owner_id AND projects.project_id = :project_id
  → project_papers（paper 收录且 selected_version_id 指向该 Version）
  → paper_versions.owner_id = :owner_id（双重校验）
  → document_parse_revisions（经 version_id）
  → chunk_sets.status = 'ready'
  → chunks
```

`paper_ids` 非 None 时（selected_papers 范围）作为同一 SQL 的 `IN` 条件；空子集等价于无候选。语义路额外排除 `embedding IS NULL`。不允许先取全量再应用层过滤（§10 不变量）。ChunkSet 属于 ParseRevision 而非 current 指针：版本重新解析后旧 Revision 的 ready ChunkSet 仍可检索，直到新 ChunkSet ready（有意取舍，保证可用性）。

#### Fake Embedding 升级（bag-of-words 哈希向量）

纯 SHA-256 哈希向量语义相似度无意义，检索实验无法做。`infrastructure/models/fake_models.py` 升级为确定性 bag-of-words 哈希（hashing trick）：英文小写分词（`[a-z0-9]+`）、去简化停用词与单字符、词经 SHA-256 映射到 1024 维桶累加词频、L2 归一化；`tests/fakes/fake_embedding_model.py` 复用同一 `bag_of_words_vector` 实现，profile 三元组不变（`("fake", "fake-embedding", 1024)`），已有 ChunkSet 不用重建。**已知限制：只表达词汇重叠，不模拟语义泛化**，用于验证管线与强过滤，不代表真实检索质量。

#### 参数与检索实验实跑结果

Settings 新增（扁平 `AGENT_` 前缀）：`AGENT_RETRIEVAL_TOP_K` / `AGENT_RETRIEVAL_PER_PAPER_LIMIT` / `AGENT_RETRIEVAL_TOKEN_BUDGET`。

校准实验 `backend/tests/evaluation/run_retrieval_eval.py`（手动运行，非自动测试）：Testcontainers pgvector 库 → 4 篇评测 PDF 经 pypdf 解析（编号标题识别为 section_heading）→ ChunkBuilder（512/64）→ 生产侧 Fake Embedding 落库（4 篇共 62 elements → 33 chunks）→ 对 manifest 中 8 道 answered 题跑 Retriever，计算期望 paper 且页码覆盖的 Chunk 是否进入最终候选：

| 轮次 | 参数 | 结果 |
|---|---|---|
| 首跑 | top_k=20 / per_paper=5 / budget=3000 | 题目级 Recall 7/8，条目级 10/11；q08 未命中 |
| 诊断 | budget=100000 仍 MISS | 排除预算原因：目标 chunk 在该论文内合并序第 6，被 per_paper_limit=5 截掉 |
| 校准后 | top_k=20 / **per_paper=8** / budget=3000 | **8/8、条目级 11/11，连续三次实跑稳定** |

最终默认值：`retrieval_top_k=20`、`retrieval_per_paper_limit=8`、`retrieval_token_budget=3000`。评测语料小（每篇 ≤10 chunks），per_paper_limit 的绝对值待真实 Provider 评测再评估；chunk 参数 512/64 本实验未暴露问题，保持不动。

#### 已知限制

- Fake Embedding 只表达词汇重叠：同义词/改写不提升相似度，Recall 结论只对管线正确性有效；
- `plainto_tsquery` 对查询词全部取 AND：自然语言长问题 FTS 常零命中（q08 实测 fts=0，走纯语义路径），首版不引入 OR 语义或查询改写；
- 语义路同距离（含零向量 NULL 距离）按 chunk_id 决胜，chunk_id 是随机 UUID，故并列候选的具体成员跨运行不稳定（Recall 结论在校准后参数下有足够余量，三次实跑一致）；
- embedding 列无向量索引（精确检索），数据量增长后性能待 Phase 4 评估；
- 评测语料仅 4 篇 33 chunks，只覆盖管线正确性；真实 Provider 的检索质量评测属切片 10。

### 切片 7：Evidence 与 Citation Validator（契约定稿）

已于 2026-08-21 实现完成。本切片只交付领域/持久化能力，不接 API、不接 rag_answer Run 执行器（切片 8 接线）。

#### 数据模型

迁移 `c5b8e2f7a3d1`（`down_revision = f2a7b3c9d4e1`，upgrade/downgrade 已在一次性容器中实跑通过）：

- `evidence`：`evidence_id`（PK）、`run_id`（FK → runs，Evidence 属于产生它的 rag_answer Run）、`project_id`、`paper_id`、`version_id`、`parse_revision_id`、`chunk_id`（FK → chunks）、`section_path`（可空）、`page_start`/`page_end`（可空）、`excerpt`（Chunk 文本摘录，截断上限 500 字符，常量 `EVIDENCE_EXCERPT_MAX_CHARS`）、`created_at`；唯一约束 `(run_id, chunk_id)`（一次 Run 中一个 Chunk 只固化一条 Evidence，Effectively Once 兜底）。paper/version/parse_revision 为 denormalize 的历史快照列，不建 FK（历史 Evidence 不因后续移出、换版或归档而改变，ADR 0002）；
- `claim_sets`：`claim_set_id`（PK）、`run_id`（FK → runs，唯一——一个 RAG Run 只提交一个 ClaimSet）、`answer_status`（`answered`/`insufficient_evidence`）、`created_at`。Message 表切片 8 才建，届时 Message 经 claim_set_id 关联；
- `claims`：`claim_id`（PK）、`claim_set_id`（FK）、`sequence`、`text`；唯一约束 `(claim_set_id, sequence)`；
- `citations`：复合主键 `(claim_id, evidence_id)`，双 FK，不存额外字段。

#### 结构化输出 Schema（定稿）

`domain/answer_schema.py`（Pydantic v2，严格 `extra="forbid"`），即切片 8 传给 ChatModel 的 `json_schema`（`rag_answer_json_schema()`）与解析校验模型（`parse_rag_answer_output(content)`，失败抛 `AnswerOutputParseError`，属可修复模型输出问题，不注册为 Run 层永久错误）：

```text
RagAnswerOutput:
  answer_status: "answered" | "insufficient_evidence"
  claims: list[ClaimDraft]        # answered 时非空；insufficient_evidence 时必须为空（Validator 校验）
ClaimDraft: { text: str, evidence_ids: list[str] }
```

条件一致性规则无法在 JSON Schema 表达，由 Citation Validator 确定性校验。

#### EvidenceService（application/evidence_service.py）

`commit_evidence(run, retrieval_results) -> list[Evidence]`：

- 校验每条结果的 `(paper_id, version_id)` 属于 Run `input_payload["version_scope"]` 快照（键名常量 `RUN_INPUT_VERSION_SCOPE_KEY`，切片 8 创建 Run 时写入）；快照缺失/形状非法或结果在快照外 → `EvidenceScopeError`（永久错误，已注册 `is_permanent_error`），不写入任何 Evidence；
- `parse_revision_id` 经 `ChunkSetRepository.get_by_id(chunk.chunk_set_id)` 解析（RetrievalResult 不携带该字段，切片 6 契约不动）；
- excerpt 截断 500 字符，不复制 Chunk 全文；
- 幂等：先 `list_by_run` 回读，已固化的 chunk_id 复用既有行，只插入新行，同一短事务提交；输入内 chunk_id 防御性去重；
- 空检索结果合法，返回空列表不写入。

#### CitationValidator（domain/citation_validator.py，纯函数）

`validate_citations(output, *, evidence, run_id) -> CitationValidationResult`（`passed` + `failures`，失败按 Claim 顺序全部收集；只含稳定 reason code 与 claim 下标，不存文本内容）。规则：

1. `answered`：claims 非空（否则 `empty_claims`）；每个段落级 Claim `evidence_ids` 非空（否则 `uncited_claim`，严格策略无例外）；
2. `insufficient_evidence`：claims 必须为空（否则 `status_mismatch`）；
3. 所有 `evidence_ids` 必须存在于本次 Run 固化的 Evidence 集合（伪造/缺失 ID → `fabricated_evidence`）；
4. 同一 Claim 内重复引用同一 Evidence 拒绝（`duplicate_citation`；不同 Claim 共享同一 Evidence 合法）；
5. 链完整性复核：Evidence 的 `run_id` 必须等于当前 Run（否则 `cross_run_evidence`；paper/version 属于快照由 EvidenceService 固化时保证）。

#### 测试要点

- Domain：`test_answer_schema.py`（8 例：合法 answered/insufficient 解析、缺字段、非法 status、claims 类型错误、额外字段拒绝、非 JSON、JSON Schema 形状稳定）；`test_citation_validator.py`（10 例：全规则与多失败顺序收集）；
- Application：`test_evidence_service.py`（7 例：字段 denormalize 与顺序、excerpt 截断、幂等重复提交、快照外 version 拒绝、paper/version 配对不符拒绝、缺快照拒绝、空结果）；
- Integration：`test_evidence_repository.py`（5 例：Evidence 往返与 `(run_id, chunk_id)` 唯一、跨 Run 隔离与 `list_by_ids`、claim_sets.run_id 唯一、claims `(claim_set_id, sequence)` 唯一、citations 复合主键与 FK 拒绝）。

#### 已知限制

- 模块笔记 `docs/learning-journal/modules/evidence-and-citation-integrity.md` 留到切片 8 接线后一并撰写（模块在 Run 编排中的实际行为届时才完整）；
- `citations` 无独立查询 API，引用详情读取随切片 8/9 落地；
- ~~历史库若执行 `alembic downgrade base`，Phase 1 迁移 `8865966463a6` 的 paper_versions 未命名 FK 会导致失败~~（已修复 2026-08-21：upgrade 与 downgrade 改用显式约束名 `paper_versions_current_parse_revision_id_fkey`，与 PostgreSQL 自动命名一致，存量库零影响，`upgrade head → downgrade base` 两个来回在一次性容器中实跑通过）。

### 切片 8：RAG Conversation（契约定稿）

已于 2026-08-21 实现完成。本切片交付 Conversation/Message 数据模型、6 个 API 端点、`ConversationService`、快照检索路径与 `RagAnswerExecutor` 全链路，并接线 Worker 分发。

#### 数据模型

迁移 `d7f3a1c9e5b2`（`down_revision = c5b8e2f7a3d1`，upgrade/downgrade 已在一次性 pgvector 容器中实跑通过）：

- `conversations`：`conversation_id`（PK）、`project_id`（FK）、`owner_id`、`title`（可空，≤200 字符，首条提问回填前 50 字符）、`scope_mode`（`project`/`selected_papers`，创建后不可改）、`active_run_id`（可空 FK → runs，单活跃 Run 认领指针）、`created_at`；
- `conversation_scope_papers`：复合主键 `(conversation_id, paper_id)` + `version_id`——`selected_papers` 模式创建时固化的默认范围；
- `messages`：`message_id`（PK）、`conversation_id`（FK）、`sequence`、`role`（`user`/`assistant`）、`content`（≤4000 字符）、`run_id`（可空 FK）、`claim_set_id`（可空 FK → claim_sets，切片 7 已建）、`created_at`；唯一约束 `(conversation_id, sequence)`。

#### API（api/conversations.py）

- `POST /api/v1/projects/{project_id}/conversations`（201）、`GET …/conversations`、`GET /api/v1/conversations/{id}`；
- `GET /api/v1/conversations/{id}/messages`：assistant 消息携带 Claim 与 Evidence 摘要（evidence_id/paper_id/version_id/section/pages/excerpt），供前端直接渲染引用；
- `POST /api/v1/conversations/{id}/messages`（202 `{user_message_id, run_id, status: "queued"}`，`Idempotency-Key` 必填，缺失 400）；
- `GET /api/v1/projects/{project_id}/evidence/{evidence_id}`：Evidence 详情（含 version_id 与页码，供 PDF 跳转）。

错误码：404 `conversation_not_found`/`evidence_not_found`；409 `project_archived`/`conversation_busy`/`project_not_indexed`；422 `invalid_scope`。Run 查询/取消/SSE 复用 `/api/v1/runs/{run_id}` 现有接口。

#### ConversationService（application/conversation_service.py）

- 创建：校验 Project 归属/归档；`selected_papers` 要求 paper_ids 非空且全部已收录、未归档、属当前 owner，并解析固化默认范围版本；`project` 模式不在创建时固化（提问时解析）；
- 提交提问：幂等键重放（复用 IdempotencyRecord，`request_hash = sha256(conversation_id:key:sha256(content))`，重放经 `run_id` 回读 User Message）→ 归档/busy/not_indexed 校验 → User Message + rag_answer Run（`input_payload` 含 `conversation_id`/`user_message_id`/`version_scope` 快照）+ `run_created` + Outbox + 幂等记录同一事务；Run 落库即推进 `event_sequence=2`（`run_created` 占用 1，与 `RunService.create_run` 语义一致——集成测试曾暴露该遗漏）；
- 单活跃 Run 双层语义：服务层预检（`active_run_id` 指向非终态 Run 直接 409；指向终态/已消失 Run 自愈清理，覆盖 QUEUED 被直接取消等未经执行器的路径）+ SQL 条件更新 `WHERE active_run_id IS NULL` 并发兜底；
- `project_not_indexed`：范围内无任何 ready ChunkSet 时快速失败，部分就绪不阻塞。

#### 快照检索（切片 6 Retriever 扩展）

新增 `ChunkRepository.search_semantic_by_scope`/`search_fulltext_by_scope` 与 `Retriever.retrieve_for_scope`：不 join `project_papers`，按 `(paper_id, version_id)` 快照集合 + `paper_versions.owner_id` + ready ChunkSet 过滤——**Paper 移出 Project 后本次 Run 仍按快照检索完**（快照语义优先）；合并逻辑抽为 `_merge_and_rank` 与 `retrieve` 共用。

#### RagAnswerExecutor（application/rag_answer_executor.py）

事务 A/B/C/D + 最终事务，模型调用全部在事务外：

1. 事务 A：持锁取消检查 + `retrieval_started`；
2. 快照检索（零结果直接走证据不足提交，不调模型）；
3. 事务 B：取消检查 + `retrieval_completed`（候选计数）；
4. `EvidenceService.commit_evidence` 固化 Evidence（幂等）；
5. 事务 C：取消检查 + `model_generation_started`；事务外经 ModelGateway 调 ChatModel（json_schema 结构化输出）→ 解析 → Citation Validator；解析或校验失败把失败原因作为反馈消息追加后**修复重试一次**，仍失败 → FAILED（`model_output_invalid`）；
6. 事务 D：`model_generation_completed`（用量）+ `citation_validation_completed`（只含 passed 与 reason code 计数）；
7. 最终事务：Assistant Message + ClaimSet + Claims + Citations + 清 `active_run_id` + Run SUCCEEDED + `answer_committed` 原子提交；`claim_sets.run_id` 唯一兜底重复提交（已有 ClaimSet 回读幂等完成，不重复建 Message）。

任何终态（SUCCEEDED/FAILED/CANCELLED）都清理 `active_run_id`；`RETRY_WAIT` 保留认领（Run 未结束，会话仍忙）。assistant content = claims 段落 `\n\n` 拼接；证据不足用固定文案 `INSUFFICIENT_EVIDENCE_TEXT`。

#### Context Token Budget（2026-08-21 定稿）

证据上下文沿用检索预算截断结果（≤ `retrieval_token_budget`）；模板 + 证据总 token 超过 `context_token_budget`（默认同检索预算 3000，tiktoken cl100k_base 精确计数）时按 rank 从低到高丢弃 Evidence，缩减一次，不循环压缩。Chat 输出上限 `AGENT_ANSWER_MAX_OUTPUT_TOKENS`（2026-08-30 从 2048 调整为 4096）。

#### Worker 接线与 Fake Chat

`worker.py` 的 `_build_model_stack` 拆出独立 `chat_backend` 开关（`AGENT_CHAT_BACKEND`，默认 `fake`）；dispatcher 注册 `RunType.RAG_ANSWER`。生产侧 `FakeChatModel` 改为**证据 ID 驱动**：Prompt 含 `evidence_id=<uuid>` 标记 → 确定性返回引用前若干个证据 ID 的合法 `answered` JSON；不含 → `insufficient_evidence`。保证本地开发与端到端测试不触网且确定性。

#### 事件

`run_created` → `run_started` → `retrieval_started` → `retrieval_completed`（candidate_count）→ `model_generation_started` → `model_generation_completed`（prompt/completion tokens）→ `citation_validation_completed`（passed + failure_reasons 计数）→ `answer_committed`（claim_set_id/answer_status/claim_count）。取消路径以 `run_cancelled` 收尾；失败路径 `run_failed`（error type + 截断消息）。事件 payload 不含问题/回答文本或证据摘录。

#### 测试要点（2026-08-21 实跑）

- Domain：`test_conversation.py`（7 例）；
- Application：`test_conversation_service.py`（24 例：scope 校验全分支、幂等重放/冲突、归档/busy/not_indexed、自愈清理、快照固化、标题回填、event_sequence 推进、消息摘要视图、Evidence 授权）、`test_rag_answer_executor.py`（13 例：answered 全链路事件序列与产物、零结果不调模型、模型返回不足、解析/校验失败修复重试、两次非法 FAILED、临时错误 RETRY_WAIT 保留认领、永久错误 FAILED 清认领、检索后/模型后取消、ClaimSet 幂等回读、run_type 防御、缺 conversation_id 永久失败）、`test_retriever.py` 追加 2 例（快照透传、空快照不调模型）；
- API：`test_conversations.py`（14 例）；
- Integration：`test_conversation_repository.py`（8 例：三表往返、唯一约束、并发 try_claim 双会话只有一个成功）、`test_chunk_retrieval.py` 追加 5 例（快照过滤、移出后仍命中、跨 owner 拒绝、非 ready 不命中、空快照）、`test_queue_worker.py` 追加 1 例（ingestion → indexing → rag_answer 三轮派发端到端，断言事件序列、citations 与 model_invocations）。

#### 已知限制

- SSE 推送与前端 Conversation UI 属切片 9；
- `project` 模式快照在每次提问时重新解析（收录变化对新提问生效，历史 Run 不变）；
- 修复重试只有一次，不做多轮自我修正；
- Context Budget 超限时只按 rank 丢弃，不做摘要压缩。

### 切片 9：API 与最小 Web UI（契约定稿）

已于 2026-08-21 实现完成。本切片不新增后端资源模型，消费切片 1/5/8 已有 REST/SSE 契约，交付 Project-scoped RAG 的用户可见闭环与延期的归档管理入口。

#### Chat 三入口与路由

- Project 页「询问整个项目」创建 `{scope_mode: "project"}` Conversation；Paper 行内「询问此篇」与 Project 文献多选「询问选中」都创建 `{scope_mode: "selected_papers", paper_ids: [...]}` Conversation；三者只调用 `POST /api/v1/projects/{project_id}/conversations`，成功后进入 `/projects/{project_id}/conversations/{conversation_id}`，不引入 owner-scoped Paper Chat；
- 多选是浏览器交互状态，空选择退化为 Project scope；已归档 Paper 不能进入 selected scope；Conversation 创建后范围不可修改；
- 对话页左侧恢复 Project 内 Conversation 列表，中间按 sequence 渲染 Message；assistant 优先按 `claims[]` 渲染段落，每条 Citation 显示稳定的引用标记，刷新后全部从 REST 恢复。

#### 提问幂等与 Run 进度

- `messageIntent` 与 Phase 1 `uploadIntent` 同构：首次提交问题时生成 `crypto.randomUUID()`；同一内容失败重试复用原 Key，内容改变生成新 Key，成功后清空意图；
- 提问返回 202 后以 `run_id` 复用 `useRunEvents`；`eventStore.KNOWN_EVENT_TYPES` 已按后端实际事件补充 `indexing_started`、`chunking_completed`、`embedding_completed`、`indexing_completed`、`retrieval_started`、`retrieval_completed`、`model_generation_started`、`model_generation_completed`、`citation_validation_completed`、`answer_committed`；
- `indexing_completed` 与 `answer_committed` 是各自 Run 的成功终态事件，收到后主动关闭 EventSource；对话页收到 `answer_committed` 后失效 Message/Conversation 查询，重新读取 PostgreSQL 已提交的回答，不从 Event payload 组装业务结果。

#### Evidence、PDF 与索引状态

- 点击引用标记按 `GET /projects/{project_id}/evidence/{evidence_id}` 打开右侧 Evidence 面板，展示 excerpt、section、page 与 Paper 快照 ID；
- PDF 继续复用浏览器原生 `<iframe src=".../file#page=N">`；以 `key={page}` 按页码重建 iframe，零新增依赖；
- Project 文献行按固定 `version_id` 查询 `index-status`，区分等待解析、等待索引、正在索引、索引就绪，并在存在 `indexing_run_id` 时链接 Run 详情。

#### 归档与错误呈现

- Project 列表与个人文献库提供 `include_archived` 开关和归档徽标；Project 页提供改名/说明修改、归档/恢复，并在归档状态显示只读提示、禁用上传/收录/移出/新建问答；
- 个人库和 Project 文献行均提供 Paper 归档/恢复；Project 行把「归档个人库资产」与「移出项目」显示为两个独立动作，并明确前者不删除 `ProjectPaper`；
- `conversation_busy`、`project_not_indexed`、`invalid_scope`、`project_archived`、`paper_archived`、`project_has_active_runs` 映射为可操作中文提示；404 统一为「资源不存在或无权访问」。

#### 测试与已知限制

- Vitest 使用 node 环境、只测纯状态逻辑：SSE 事件归并/终态、Phase 2 具名事件清单、提问幂等 Key、错误文案和 scope 选择；不挂 DOM；
- 2026-08-21 实跑：`cd web && npm test` → 59 passed；`cd web && npm run build` → TypeScript strict 与 Vite 构建通过；`cd backend && .venv/bin/pytest tests -q --ignore=tests/integration` → 366 passed、4 skipped；
- 本切片不做 Playwright E2E、真实 Provider Smoke 或评测实跑，统一留到切片 10；回答不做 token 流式输出；索引状态按文献行独立查询，适合当前最小列表规模，批量状态接口留到有性能证据后再评估。

### 切片 10：验收复盘与 Phase 2 收口（契约定稿）

已于 2026-08-21 完成。本切片不新增产品功能，只补可复现验收入口、真实组件显式 Smoke、Phase 2 E2E，并审计既有可靠性覆盖。

#### 固定评测契约与实跑

- `tests/evaluation/run_phase2_eval.py` 复用正式 `IngestionService`、三个 Executor、Retriever、Evidence 与 Citation Validator，在一次性 pgvector 数据库中跑 manifest 全部 14 题；报告记录时间、Provider/profile、参数、逐题指标与限制；
- 正式 Fake Parser 会忽略输入 PDF 并返回固定结构，不能验证 manifest 页码事实，因此完整确定性评测使用正式本地 `PypdfDocumentParser`；Embedding/Chat 保持 Fake，真实 Docling 由独立 opt-in Smoke 覆盖；
- 参数为 chunk 512/64、Top-K 20、每篇上限 8、预算 3000。实跑结果：answered Retrieval 8/8，must-cite Retrieval 11/11，Citation completeness 11/11，Validator 14/14，selected scope 3/3；状态匹配 8/14（answered 8/8、insufficient 0/6）；
- insufficient 0/6 是 Fake Chat 的已知能力边界：只要有任意 Evidence 就返回 answered，不能判断语义证据充分性。不得解释为真实模型质量，也未报告 Groundedness、性能或 Provider 质量。

#### 真实组件与 Provider

- Docling opt-in：清除本机不完整 SOCKS 代理环境后，2 passed；本机 CUDA 驱动不兼容时自动 CPU fallback，并有 Docling 弃用告警；
- Embedding opt-in：真实 `embedding-3` 返回 1 个 1024 维向量，usage 非空；Base URL 必须为 API 根，Adapter 自行追加 `/embeddings`；
- Chat opt-in：当前真实模型不支持 `response_format=json_schema`；新增 `AGENT_CHAT_JSON_SCHEMA_SUPPORTED`（默认 true），显式 false 时使用 `json_object`，输出仍经本地 `RagAnswerOutput` 校验。真实结构化 Chat Smoke 通过且 usage 非空；
- 普通测试继续默认 Fake、不联网；Key、Prompt 全文、向量和敏感响应未写入日志、Event 或报告。

#### E2E 与故障证据

- `web/e2e/phase-02.spec.ts` 覆盖创建 Project、导入 PDF、等待 ingestion/indexing、Project 问答与 RAG SSE、刷新恢复、Citation → Evidence → PDF `#page=N`、单篇 selected scope、Project 归档只读；与 Phase 1 同跑共 2 passed；
- E2E 发现并修复 Project 归档按钮 active 状态误调用 restore 的真实缺陷；测试使用 Fake Parser/Embedding/Chat，不产生费用；
- 对幂等/重复执行、busy 自愈、Provider 错误分类、结构修复、取消、最终原子性、终态 Event、SSE 重放、归档/隔离/scope/Evidence 授权逐项对照现有测试，未发现需要复制断言的缺口。证据矩阵见 `../modules/rag-evaluation.md`。

#### 完整验证结果

- Web：Vitest 59 passed；TypeScript strict + Vite build 通过；Playwright Phase 1–2 共 2 passed；
- Backend：非集成 370 passed、4 skipped；integration 79 passed；`ruff check src tests` 通过；pyright 0 errors；
- Evaluation：指标单元与 Fixture 6 passed；完整 14 题 runner 成功；
- Provider：默认入口 2 skipped；真实 Docling 2 passed；真实 Embedding 1 passed；真实结构化 Chat 1 passed。

## 测试方式

- **Domain**：Chunk/Profile 哈希、Claim/Evidence 关系和 Citation Validator；
- **Application**：索引复用、RAG 编排、无证据、重复 Job、取消和最终原子提交；
- **PostgreSQL**：pgvector/FTS、唯一约束、Project 强过滤和并发提交；
- **API/SSE**：Project/Paper 归档限制、整个 Project/单篇/多篇 Conversation、幂等提问、刷新恢复、取消和引用跳转；
- **Provider Contract**：Fake Embedding/Fake Chat 为默认，真实 Provider 显式启用；
- **Evaluation**：固定 Project、问题、期望 Paper/Evidence 和明确无答案问题。

普通测试不得访问真实模型。评测至少记录 Retrieval Recall@K、Citation validity/completeness；Groundedness 只有实际人工核对后才记录。本次未运行 Groundedness，不使用虚构质量指标。

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

2026-08-21 对照结论：以上条件全部满足。真实 Provider 只证明最小 Adapter/结构契约可调用，不代表真实语料质量或生产可用；Fake 评测暴露的 insufficient 0/6 作为模型能力限制保留，不影响“无检索结果时确定性返回证据不足”的产品不变量与自动测试。

## 实现前需要确定

以下事项已于 2026-08-20 定稿：Provider 方案（OpenAI-compatible Adapter + Fake）、pgvector 镜像替换（本地库可重建）、`tiktoken` 引入、scope 模型（两值、不可改）、段落级 Claim 严格绑定、indexing Run 创建时机、Worker run_type 分发、评测语料用合成 PDF。详见「已确定事项」。

已随切片实验确定：Chunk 参数 512/64（切片 4 设定起点，切片 6 实验未暴露问题，保持不动）；Embedding/Chat Model 与维度（切片 3/5：智谱 embedding-3 @1024、DeepSeek deepseek-v4-flash）；Top-K/每篇上限/Token 预算（切片 6：20/8/3000）；评测语料与问题集（切片 2）。

仍在对应切片确定，不阻塞当前阶段边界：

~~1. Context Token Budget 细节（切片 8）。~~ 已定稿（2026-08-21，见「切片 8」小节）：证据上下文沿用检索预算截断，超预算按 rank 从低到高丢弃一次；`AGENT_ANSWER_MAX_OUTPUT_TOKENS` 于 2026-08-30 调整为默认 4096。

## 已确定事项

- 2026-08-20：承认 `DocumentContentReader` 未作为独立 Port 落地，现状为应用服务 `DocumentQueryService`（授权读路径）+ Worker 内直接使用 `ElementRepository`；更新本 Spec 与 Phase 1 Spec，不新建 Port；
- 2026-08-20：Worker 新增按 `run_type` 显式分发的组合 Executor，领域层引入 `RunType` 枚举，未知类型显式失败；
- 2026-08-20：indexing Run 由 IngestionExecutor 结果提交事务内随解析成功原子创建（Run + Outbox 同事务），不引入独立扫描循环；ChunkSet 属于 ParseRevision，跨 Project 复用；
- 2026-08-20：Provider 方案为 `EmbeddingModel`/`ChatModel` 窄 Port + 基于 httpx2 的 OpenAI-compatible Adapter + 确定性 Fake；不引入 openai/LangChain SDK；Provider Key 只来自服务端配置；Adapter 层对 429/5xx/超时最多 2 次短重试，耗尽交 Run 层；
- 2026-08-20：新增依赖 `pgvector`（SQLAlchemy 绑定）与 `tiktoken`，各自独立 `chore:` 提交；compose 与 Testcontainers 镜像从 `postgres:18` 换为 `pgvector/pgvector:pg18`，本地开发库允许重建，迁移负责 `CREATE EXTENSION vector`；
- 2026-08-20：`scope_mode` 只有 `project` / `selected_papers` 两值，单篇即一条的 `selected_papers`；Conversation 创建后 scope 不可改；Run `input_payload` 固化 `[{paper_id, version_id}, ...]` 快照；
- 2026-08-20：Citation 严格策略——Claim 为段落级，`answered` 状态下每个 Claim 必须至少绑定一个本次 Run 的 Evidence，否则修复重试一次后 FAILED；
- 2026-08-20：归档语义——归档 Paper 后同哈希上传仍复用已有 canonical Version，不自动恢复归档；
  归档 Project 存在非终态 Run 时归档返回 409；永久删除原定 Phase 4，后由 ADR-0004 推迟；
- 2026-08-20：FTS 语言配置 `english`；RRF `k=60`；不做 Query Expansion 和独立 reranker；
- 2026-08-20：评测语料使用合成 PDF（切片 2 由子智能体构建），问题集覆盖单篇事实、跨篇综合、明确无答案和范围边界四类；指标只报告实跑的 Retrieval Recall@K、Citation validity/completeness 和少量人工 Groundedness。

## 预期学习笔记

- `docs/learning-journal/modules/model-gateway.md`；
- `docs/learning-journal/modules/hybrid-retrieval-and-pgvector.md`；
- `docs/learning-journal/modules/evidence-and-citation-integrity.md`；
- `docs/learning-journal/modules/rag-conversation.md`。
