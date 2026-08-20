# Phase 2：有引用的 RAG 文献问答

## 状态

待开始。本文为阶段实施 Spec，建立在 Phase 1 完成 Project、文献库、可靠导入和结构化 Element 层之后。

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

## 前置条件

Phase 1 正式验收前先完成以下收口：

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

- `DocumentContentReader`：按授权上下文读取指定 ParseRevision 的 Element 和来源定位；
- `ChunkBuilder`：把 Element 组合成适合检索的 Chunk；
- `EmbeddingModel`：批量生成向量并返回 Usage；
- `ChatModel`：生成结构化 Answer/Claim/Evidence ID；
- `Retriever`：执行 Project-scoped Hybrid Retrieval；
- `EvidenceService`：把检索结果固化为可引用 Evidence；
- `CitationValidator`：校验 Claim 与 Evidence 的结构和权限关系；
- `RagAnswerService`：编排 Message、Retrieval、模型调用和最终提交。

Phase 2 不引入 LangGraph。ARQ Job 仍只携带 `run_id`，Worker 根据 `run_type` 调用 `ingestion`、`indexing` 或 `rag_answer` Executor。

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
└─ Conversation
   ├─ User Message
   └─ Assistant Message
      └─ ClaimSet
         └─ Claim ── Citation ── Evidence ── Chunk
```

主要新增模型：

- `chunk_sets`：`parse_revision_id`、chunk/embedding profile、状态、错误和完成时间；
- `chunks`：顺序、检索文本、token 数、章节、页码范围、内容哈希、`tsvector` 和向量；
- `chunk_element_links`：Chunk 到 Element 的稳定映射和顺序；
- `conversations`：Project、owner、标题和时间；
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
- Chunk/Profile 变化产生新 ChunkSet，旧索引保留直到无引用后再清理；
- Phase 2 只配置一个活动 Chunk/Embedding Profile；
- 首版使用 pgvector 精确检索，不创建 HNSW；
- 新 ParseRevision 成功后创建独立 `indexing` Run；已有相同 ready ChunkSet 时直接复用；
- Project 收录已有 Paper 时不重复切分或 Embedding。

具体 Chunk 长度、Overlap、Embedding 模型和向量维度在第一个检索切片的小实验中确定，并记录到本 Spec，不提前固定。

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

首版不做 LLM Query Expansion。Retriever 必须先限制 `owner_id`、`project_id`、`ProjectPaper`、`selected_version_id` 和 ready ChunkSet，再进行排序，不能在检索后仅靠应用层删除越权结果。

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

无可用证据属于成功的 `insufficient_evidence` 业务结果，不是系统失败。

## Citation Validator

运行时确定性校验：

- Evidence 存在且属于当前 owner/Project；
- Evidence 的 Paper 当前或历史上属于该 Project 的本次可见快照；
- PaperVersion、ParseRevision、Chunk 和来源定位链完整；
- 模型只能使用本次 Run 提供的 Evidence ID；
- 重要 Claim 至少绑定一个 Evidence；
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

## Event 方向

新增小型事件：

- `indexing_started`、`chunking_completed`、`embedding_completed`、`indexing_completed`；
- `retrieval_started`、`retrieval_completed`；
- `model_generation_started`、`model_generation_completed`；
- `citation_validation_completed`；
- `answer_committed`、`model_usage_recorded`。

Event 不保存完整问题、Prompt、Chunk 文本、Evidence 摘录或最终回答；客户端收到 `answer_committed` 后重新查询 Message。

## 失败、重试和取消

- Provider 429、5xx 和网络超时属于临时错误，由 Provider 层进行少量短重试，耗尽后交给 Run 重试；
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

1. **Phase 1 收口**：ProjectPaper、`selected_version_id`、文件去重和 Paper Library API；
2. **阶段契约与评测 Fixture**：确定最小模型、错误码和固定问题集；
3. **Model Gateway**：Embedding/Chat Port、Fake Provider、错误分类和 Usage；
4. **ChunkSet**：结构感知 Chunk、Element 映射、迁移和确定性测试；
5. **Indexing Run**：pgvector、批量 Embedding、复用、重试和取消；
6. **Hybrid Retrieval**：FTS、向量检索、RRF、Project 隔离和上下文预算；
7. **Evidence/Citation**：Evidence、Claim、Citation 和 Validator；
8. **RAG Conversation**：Conversation、Message、后台回答 Run 和最终提交；
9. **API 与最小 Web UI**：Chat、Run 进度、引用详情和 PDF 跳转；
10. **验收复盘**：评测、Provider Smoke、故障测试和学习笔记。

## 测试方式

- **Domain**：Chunk/Profile 哈希、Claim/Evidence 关系和 Citation Validator；
- **Application**：索引复用、RAG 编排、无证据、重复 Job、取消和最终原子提交；
- **PostgreSQL**：pgvector/FTS、唯一约束、Project 强过滤和并发提交；
- **API/SSE**：Conversation、幂等提问、刷新恢复、取消和引用跳转；
- **Provider Contract**：Fake Embedding/Fake Chat 为默认，真实 Provider 显式启用；
- **Evaluation**：固定 Project、问题、期望 Paper/Evidence 和明确无答案问题。

普通测试不得访问真实模型。评测至少记录 Retrieval Recall@K、Citation validity/completeness 和少量人工 Groundedness 结果；不使用虚构质量指标。

## 阶段完成条件

- ParseRevision 可以生成版本化、可复用的 ready ChunkSet；
- 同一 Paper 跨 Project 不重复解析、Chunking 或 Embedding；
- Hybrid Retrieval 只返回当前 Project 可见文献；
- 回答只能引用本次 Run 的有效 Evidence；
- 无证据问题明确返回证据不足；
- Conversation、Message、Run 和 Citation 刷新后可恢复；
- 引用可以跳转到 PaperVersion、Element 和 PDF 页码；
- Provider 临时错误、重复 Job、取消和 SSE 重连有自动测试；
- 有固定 Retrieval/Citation 评测数据和真实运行报告；
- 阶段 Spec、模块学习笔记和已知限制已更新。

## 实现前需要确定

以下参数在对应切片的小实验中确定，不阻塞当前阶段边界：

1. Chunk 长度、Overlap 和表格处理规则；
2. 首个 Embedding/Chat Model 及向量维度；
3. semantic/FTS Top-K、RRF 参数和每篇论文上限；
4. Context Token Budget 和结构化输出 Schema 细节；
5. 小型评测集使用的公开或合成论文。

## 预期学习笔记

- `docs/learning-journal/modules/model-gateway.md`；
- `docs/learning-journal/modules/hybrid-retrieval-and-pgvector.md`；
- `docs/learning-journal/modules/evidence-and-citation-integrity.md`；
- `docs/learning-journal/modules/rag-conversation.md`。
