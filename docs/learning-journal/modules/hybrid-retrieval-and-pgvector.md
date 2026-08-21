# Hybrid Retrieval 与 pgvector（Retriever + RRF + 强过滤链）

Phase 2 切片 6 完成后成文（2026-08-21）。

## 解决的问题

切片 5 之后，文献库有了带 `embedding`（pgvector 精确检索）和 `search_vector`（english tsvector）的 Chunk 索引，但还没有任何查询路径。本模块交付 Project 范围内的混合检索：语义与全文两路 Top-K、RRF 合并、每篇论文上限与 Token 预算截断，并把 RAG 最敏感的安全不变量——**检索结果只属于当前 owner 的当前 Project（或其选中的 Paper 子集）**——落在 SQL 内而不是应用层事后过滤。

## 边界与执行流程

```text
Retriever.retrieve(owner_id, project_id, query, selected_paper_ids?, run_id?)
  ├─ 空查询（含纯空白）→ ValueError，不调模型不查库
  ├─ ModelGateway.embed([query])           # 事务外；记录 model_invocations（run_id 可空，切片 8 填）
  ├─ 一只读短事务内两路 SQL（ChunkRepository）：
  │    ├─ search_semantic：embedding <=> :query_vector cosine 升序 Top-K（排除 embedding IS NULL）
  │    └─ search_fulltext：search_vector @@ plainto_tsquery('english', q)，ts_rank 降序 Top-K
  └─ domain 纯函数：rrf_merge(k=60) → apply_per_paper_limit(8) → apply_token_budget(3000)
       → RetrievalResult[]（chunk、paper_id、version_id、两路 rank、rrf_score、最终 rank）
```

- Route 不涉及（本切片无 API）；Retriever 是 application 服务，SQL 全在 Repository；
- 调用方负责提供 owner/project 上下文（切片 8 的 rag_answer Run）；Retriever 自身不做授权判定，靠 SQL 强过滤保证范围。

## 状态、数据模型和事务

本切片不新增表。检索只读：两路查询共用一条只读短事务，不写任何业务状态；唯一的写是 ModelGateway 的 `model_invocations` 调用记录（独立短事务，失败只记日志）。

强过滤链（两路 SQL 共用 `_scoped_select`）：

```text
projects.owner_id = :owner_id AND projects.project_id = :project_id
  → project_papers（收录关系且 selected_version_id 指向该 Version）
  → paper_versions.owner_id = :owner_id（双重校验）
  → document_parse_revisions → chunk_sets.status = 'ready' → chunks
```

`selected_papers` 范围是同一 SQL 上的 `paper_id IN (...)` 条件，空子集等价于无候选。

## 关键决定与替代方案

- **强过滤在 SQL 内，不在应用层**（§10 不变量）：越权/未收录/未选中版本/非 ready ChunkSet 的 Chunk 物理上不会进入候选集，杜绝「先取回再删」的窗口；代价是两路查询各带一条 5 表 JOIN 链，语料规模下可接受（精确检索本来就全扫范围内行）。
- **RRF（k=60）而非分数加权**：两路分数量纲不同（cosine 距离 vs ts_rank），RRF 只看排名，无需校准权重；平局按 semantic rank → fts rank → chunk_id 稳定决胜，保证确定性。
- **合并/上限/预算是 domain 纯函数**：`rrf_merge`、`apply_per_paper_limit`、`apply_token_budget` 不依赖任何 IO，单测覆盖数值与边界（双路命中排序提升、预算贪心跳过超限候选、预算 0 返回空）。
- **预算贪心填充而非硬停**：超出剩余预算的候选跳过但继续扫描后续更小候选，避免一个超大 Chunk（如整页表格）堵死预算。
- **检索链不过滤 `current_parse_revision_id`**：ChunkSet 属于 ParseRevision；版本重新解析后旧 Revision 的 ready ChunkSet 仍可检索，直到新索引 ready（可用性优先的有意取舍）。
- **Fake Embedding 升级为 bag-of-words 哈希向量**：原纯哈希向量语义相似度无意义，检索实验做不了。现实现为 hashing trick（小写分词、去简化停用词、词哈希到 1024 桶、L2 归一化），生产 fake 与 `tests/fakes` 共用同一 `bag_of_words_vector`；profile 三元组不变，已有 ChunkSet 无需重建。**它只表达词汇重叠，不模拟语义泛化**——这是开发/测试假实现，不是检索质量声明。
- **参数经实验校准**：`top_k=20` / `per_paper_limit=8` / `token_budget=3000`（`AGENT_RETRIEVAL_*` 扁平配置）。per_paper_limit 从起始值 5 调到 8：评测 q08 的目标 chunk 在其论文内合并序第 6，被 5 截掉；诊断（budget=100000 仍 MISS）排除了预算原因后才调整上限，非盲目调参。

## 失败、重试、重复和取消行为

- 空查询：快速 `ValueError`，不产生任何模型调用或检索（切片 8 的上层决定如何映射为业务结果）；
- Embedding 失败：ModelGateway 记录 failed invocation 后原样抛出，不发起检索；错误分类沿用切片 3（临时/永久），重试策略由切片 8 的 Run 层接管；
- FTS 零命中是正常路径：退化为纯语义结果（fts_rank 全 None）；两路都空则返回空列表，由上层给出 `insufficient_evidence`；
- 本切片无后台 Run，取消/重试不适用；重复调用无副作用（只读 + 调用记录）。

## 安全和可观测性

- 所有检索路径同时限制 owner 与 Project（双重 owner 校验：projects 与 paper_versions）；
- 日志只记录候选数量摘要（semantic/fts/merged/final 计数），不记录问题文本、Chunk 内容或分数；
- 查询向量调用经 ModelGateway 记录（provider/model/usage/延迟/错误分类，含 run_id 接线位），不存 Prompt；
- `search_vector` 固定 english 配置，中文分词不在本阶段范围（2026-08-20 定稿）。

## 重要测试和运行结果

- Domain `tests/domain/test_retrieval.py`（9 例）：RRF k=60 数值、单/双路命中与排序提升、稳定决胜、paper/token 字段携带、每篇上限保序、预算贪心截断、非法参数；
- Integration `tests/integration/test_chunk_retrieval.py`（9 例，pgvector 容器）：cosine 排序与 limit、null embedding 排除、ts_rank 排序与词干化、跨 owner/跨 Project/未收录不出现、未选中 Version 不出现、running/failed ChunkSet 两路均不出现、selected_papers 子集与空子集、结果字段完整；
- Application `tests/application/test_retriever.py`（7 例）：编排与 invocation 记录（含 run_id）、空查询、FTS 零命中纯语义路径、上限+预算组合、范围透传、Embedding 失败传播、非法参数；
- Fake 模型 `tests/infrastructure/test_fake_models.py`（7 例）：确定性、L2 归一化、词汇重叠相似度、零向量；
- 校准实验 `tests/evaluation/run_retrieval_eval.py` 实跑（非自动测试）：4 篇语料 62 elements → 33 chunks，answered 8 题——起始参数 7/8（q08 被每篇上限截掉），校准 per_paper_limit=8 后 **8/8、条目级 11/11，连续三次实跑一致**；
- 切片完成时：非集成 281 passed + 4 skipped，integration 60 passed，ruff/pyright 全绿。

## 代码入口

- 领域：`domain/retrieval.py`（RRF/上限/预算纯函数、`RetrievedChunk`）
- 端口：`application/ports/chunk_repository.py`（`search_semantic`/`search_fulltext`）
- 服务：`application/retriever.py`（`Retriever`、`RetrievalResult`）
- 适配器：`infrastructure/persistence/chunk_repository.py`（`_scoped_select` 强过滤链）
- Fake：`infrastructure/models/fake_models.py`（`bag_of_words_vector`）、`tests/fakes/fake_embedding_model.py`
- 配置：`infrastructure/config.py`（`AGENT_RETRIEVAL_TOP_K`/`_PER_PAPER_LIMIT`/`_TOKEN_BUDGET`）
- 评测：`tests/evaluation/run_retrieval_eval.py`

## 已知限制

- Fake Embedding 只表达词汇重叠，Recall 结论只对管线正确性有效；真实 Provider 检索质量评测属切片 10；
- `plainto_tsquery` 对查询词取 AND：自然语言长问题 FTS 常零命中（q08 实测 fts=0，纯语义路径兜底）；首版不引入 OR 语义、查询改写或独立 reranker；
- 语义路同距离候选按随机 chunk_id 决胜，并列集合的成员跨运行不稳定（校准后参数下 Recall 有余量，三次实跑一致）；
- embedding 列无向量索引，精确检索随语料增长的性能待 Phase 4 评估是否引入 HNSW；
- 评测语料仅 4 篇 33 chunks；per_paper_limit=8 的绝对值在小语料上校准，真实论文（数百 chunks）下待切片 10 复评。

## 60 秒面试说明

"混合检索模块把安全问题放在第一位：两路 SQL 都带同一条五表 JOIN 强过滤链——owner、Project、收录关系、selected_version、ready ChunkSet 全部在 SQL 里过滤，越权数据物理上进不了候选集，而不是取回来再在应用层删。语义路用 pgvector cosine 精确检索，全文路用 tsvector + plainto_tsquery，两路排名用 RRF（k=60）合并——选 RRF 是因为两路分数量纲不同，排名融合不需要校准权重。合并、每篇上限和 Token 预算都是领域纯函数，确定性好测。为了让不触网的检索实验有意义，我把 Fake Embedding 从纯哈希升级成 bag-of-words 哈希向量——只表达词汇重叠，但确定且足够验证管线。最后用 4 篇合成语料 8 道题实跑校准参数：发现每篇上限 5 会截掉目标 chunk，诊断排除预算因素后调到 8，三次实跑稳定 8/8。"
