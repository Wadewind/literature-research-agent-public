# Chunk 与索引模块（ChunkSet + ChunkBuilder + IndexingExecutor）

Phase 2 切片 4（chunking 与 Worker 分发）和切片 5（Embedding 与 pgvector）完成后成文。

## 解决的问题

Phase 1 的 Element 层是结构化文档事实，不能直接用于检索：检索需要面向模型上下文和 Embedding 重新组织的消费单元（Chunk），以及可复用、可重建、版本化的索引。本模块把固定 Parse Revision 转成版本化 ChunkSet（Chunk + Element 映射 + tsvector + 向量），并保证同一论文跨 Project 不重复切分或 Embedding。

## 边界与执行流程

```text
IngestionExecutor 结果提交事务（含复用路径）
  └─ 同事务创建 indexing Run + run_created + QueueOutbox
        │
Worker：RunExecutionService → RunDispatcher（按 run_type 分发）
  └─ IndexingExecutor.execute
      ├─ 事务 A：按 (parse_revision_id, profile_hash) 查 ChunkSet
      │    ├─ ready → 复用，Run SUCCEEDED + indexing_completed(reused)
      │    ├─ failed/running 遗留行 → 重置复用同一行
      │    └─ 无 → 创建 running 行 + indexing_started
      ├─ 短事务分页读 Element + 来源定位（500/批）
      ├─ 事务外：ChunkBuilder.build_chunks → ChunkDraft 列表
      ├─ 事务 C：chunks + chunk_element_links 原子提交
      │    （ChunkSet 仍 running）+ chunking_completed
      ├─ Embedding 阶段（循环，批 AGENT_EMBEDDING_BATCH_SIZE=32）：
      │    持锁检查取消 + 读 embedding 为 null 的批次
      │    → 事务外 ModelGateway.embed（记录 model_invocations，含 run_id）
      │    → 短事务写回向量
      └─ 最终事务：ChunkSet ready + Run SUCCEEDED
           + embedding_completed + indexing_completed
```

- `ChunkBuilder` 是 domain 纯函数，输出不含持久化 ID 的 `ChunkDraft`，确定性可测；
- ChunkSet 属于 Parse Revision 而非 Project：同一解析结果跨 Project 复用索引，收录不触发任何切分/Embedding；
- 模型调用严格在数据库事务外；每个短事务只做一件事；取消检查在事务 A/C 入口和每个 Embedding 批次前。

## 状态、数据模型和事务

- `chunk_sets`：`chunk_set_id`、`parse_revision_id`（FK）、`profile_hash`、`config`、`status`（`running`/`ready`/`failed`）、`error`、`created_at`、`completed_at`；唯一约束 `(parse_revision_id, profile_hash)`——与 ParseRevision 相同的「唯一约束即幂等键」模式，重复 Job/重跑只能产生一行。
- `chunks`：`chunk_id`、`chunk_set_id`、`sequence`、`text`、`token_count`、`section_path`、`page_start`/`page_end`、`content_hash`、`embedding vector(1024)` 可空、`search_vector` tsvector 生成列（`to_tsvector('english', text)` STORED + GIN 索引）；唯一约束 `(chunk_set_id, sequence)`。
- `chunk_element_links`：复合主键 `(chunk_id, element_id)` + `sequence`；`element_id` 有普通索引支持反查。
- `ChunkProfile` 七字段（max_tokens 512 / overlap_tokens 64 / tokenizer cl100k_base / include_section_prefix / embedding_provider/model/dimensions）共同参与规范化 JSON 的 `profile_hash`；profile 变化产生新 ChunkSet，旧索引保留。
- 向量维度在迁移期固定 1024，与 `AGENT_EMBEDDING_DIMENSIONS` 默认值一致；改维度需要新迁移（有意取舍）。

## 关键决定与替代方案

- **chunk 与 embedding 一个 profile hash**：首版不拆分独立的向量索引平台，ChunkSet 同时固定切分和向量配置，复用判断一次到位；代价是换 Embedding 模型会整体重建 ChunkSet（个人项目可接受）。
- **整 Element 重叠回带**：相邻 Chunk 的 overlap 按整 Element 回带，不切半个 Element，保证每个 Chunk 的 Element 映射和页码回溯完整；单个超限 Element（大表格）允许独立成 Chunk 存在，不硬切。
- **章节标题作前缀而非 Chunk**：`section_heading` 是 Chunk 边界，标题文本拼入后续 Chunk 开头并计入 token_count，让孤立段落保留章节语境；caption 并入 table/figure 父 Element 同 Chunk；页眉页脚与空文本 Element 不进入索引。
- **chunks 与 embedding 分事务提交**：chunking 结果先落库，Embedding 分批写回——长模型调用不持有事务，崩溃/取消后重跑只补 `embedding IS NULL` 的批次，chunks 由唯一约束兜底不重复。代价：取消后 ChunkSet 停在 running，靠下一次触发补齐。
- **fake/真实 backend 开关**：`AGENT_EMBEDDING_BACKEND=fake|openai_compatible`（默认 fake，仿 `AGENT_PARSER_BACKEND`）；fake 模式下 profile 三元组固定 `("fake", "fake-embedding", 1024)`，避免 fake 产出的 ChunkSet 与真实 profile 混淆。
- **tsvector 用生成列**：`to_tsvector('english', text)` STORED，写入路径零额外代码，FTS 配置随列定义固定为 english（语料为英文论文）。
- **RunDispatcher 显式分发**（同属切片 4）：Worker 按 `run.run_type` 分发到 ingestion/indexing 执行器，未知类型推进 FAILED（`unknown_run_type`）不静默执行；`RunExecutionService` 单 executor 签名不变。

## 失败、重试、重复和取消行为

- Parse Revision 不存在/未成功：永久输入错误（`IndexingInputError`），Run 直接 FAILED 且不创建 ChunkSet；
- revision 成功但零 Element：合法空文档，产生空 ChunkSet 并 ready，不调模型；
- 模型临时错误（429/5xx/超时，切片 3 分类）：ChunkSet failed + Run RETRY_WAIT + Outbox 退避重投；永久错误（auth/invalid_request/response）→ FAILED；
- 重跑 failed/running 遗留行：复用同一行重置，chunks 已存在则跳过 chunking 只补 null 向量；
- 取消：批次间检查 `CANCEL_REQUESTED` → CANCELLED，已写向量保留；
- 重复 Job：RunExecutionService 只认领 QUEUED；ChunkSet/Chunk 唯一约束兜底。

## 安全和可观测性

- 索引构建在 Worker 内按 `revision_id` 直接读 Element（所有权已在 ingestion 链路上校验）；对外读路径（index-status API）走 owner → Project → ProjectPaper → Version 完整授权链，越权统一 404；
- Event 只记录 chunk 数、profile_hash、token 用量摘要，不含 Chunk 文本；
- Embedding 调用经 ModelGateway 记录 `model_invocations`（含 run_id、usage、延迟、错误类型），不存 Prompt。

## 重要测试和运行结果

- Domain：`test_chunk_profile.py`（哈希确定性、参数校验）、`test_chunk_builder.py`（分组/重叠/表格题注/章节前缀/排除规则/页码/空文档）；
- Application：`test_indexing_executor.py`（全链路事件、复用、重跑只补 null、取消竞争、错误分类、空 ChunkSet、invocation 记录）、`test_run_dispatcher.py`、ingestion 触发 indexing；
- Integration：`test_chunk_repository.py`（约束与查询、向量往返、cosine `<=>` Top-K、tsvector `@@` 命中/不命中）、`test_queue_worker.py` 端到端（上传 → ingestion → 自动 indexing → ChunkSet ready、chunks 带 embedding/search_vector）、`test_index_status.py`（API 各分支）；
- 切片 5 完成时：非集成 258 passed + 4 skipped，integration 51 passed（pgvector/pgvector:pg18 容器），ruff/pyright 全绿，迁移 upgrade/downgrade 实跑通过。

## 代码入口

- 领域：`domain/chunk_profile.py`、`chunk_builder.py`、`chunk.py`、`run.py`（RunType）
- 端口：`application/ports/chunk_set_repository.py`、`chunk_repository.py`
- 服务：`application/indexing_executor.py`、`run_dispatcher.py`、`document_query_service.py`（get_index_status）
- 适配器：`infrastructure/persistence/chunk_set_repository.py`、`chunk_repository.py`、`infrastructure/models/fake_models.py`
- Worker 接线：`worker.py`（`_build_chunk_profile`、`_build_model_stack`、dispatcher 装配）
- 路由：`api/documents.py`（index-status）
- 迁移：`migrations/versions/e9c4d2f8a1b7_create_chunk_tables.py`、`f2a7b3c9d4e1_chunks_增加_embedding_与_search_vector.py`

## 已知限制

- 向量维度固定 1024，改维度需要新迁移；embedding 列无向量索引（精确检索，数据量大了再评估 HNSW）；
- 取消后 ChunkSet 停在 running，需下一次触发补齐（无独立恢复任务）；
- 真实 Provider 端到端冒烟需 `AGENT_RUN_PROVIDER_TESTS=1` 显式启用，默认套件不触网；
- Chunk 参数（512/64）是实验起点，待切片 6 检索实验校准；
- 本地开发库换 pgvector 镜像后需重建数据卷。

## 60 秒面试说明

"索引模块把 Phase 1 的 Parse Revision 变成版本化、可复用的检索索引。核心复用了 Phase 1 验证过的模式：`(parse_revision_id, profile_hash)` 唯一约束撑起 Effectively Once——重复 Job 进来直接复用 ready ChunkSet；切分和向量配置共用一个 profile hash，换配置即新 ChunkSet。ChunkBuilder 是纯函数，按整 Element 重叠、表格题注不拆、章节标题作前缀，保证每个 Chunk 都能回溯 Element 和页码。事务上 chunking 先落库、Embedding 分批事务外调用再写回，崩溃或取消后重跑只补缺失向量。触发上 indexing Run 在 ingestion 提交事务内与 Outbox 原子创建，解析成功必然跟随索引，不需要独立扫描循环。同一论文跨 Project 复用索引，Project 收录零计算成本。"
