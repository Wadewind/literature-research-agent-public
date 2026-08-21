# Phase 2 RAG 评测与验收

Phase 2 切片 10 完成后成文（2026-08-21）。

## 解决的问题

本模块把“单元测试通过”提升为可复现的阶段验收：固定 4 篇合成 PDF 与 14 道题，走正式导入、索引、检索、Evidence、结构化回答和引用提交管线，分别验证工程闭环、引用完整性与 scope 隔离。Fake 结果只代表确定性管线，不代表真实模型质量。

## 评测边界与流程

```text
manifest + 4 PDFs
  → IngestionService / IngestionExecutor（PypdfDocumentParser）
  → IndexingExecutor（Fake Embedding，1024 维）
  → Conversation + rag_answer Run
  → Retriever → Evidence → Fake Chat → Citation Validator → 原子提交
  → 对照 manifest 汇总 JSON 报告
```

Runner 为 `backend/tests/evaluation/run_phase2_eval.py`，使用一次性 `pgvector/pgvector:pg18`，不连接开发数据库。它没有另建平行评测管线，而是对正式执行器加一个只记录本次检索结果的薄包装。生产 Fake Parser 会忽略 PDF 字节并返回固定中文结构，无法定位 manifest 事实，因此完整评测使用正式本地 fallback `PypdfDocumentParser`；Docling 通过独立显式 Smoke 验证。

复现命令：

```bash
cd backend
.venv/bin/python tests/evaluation/run_phase2_eval.py \
  --json-output /tmp/phase-02-evaluation.json
```

配置：pypdf 6.16.1；Fake Embedding `fake-embedding`；Fake Chat `fake-chat`；chunk 512、overlap 64、Top-K 20、每篇上限 8、检索预算 3000。报告记录起止时间、manifest 版本、Provider/profile、参数、逐题结果和限制，不记录 Prompt、全文或敏感响应。

## 2026-08-21 实跑结果

| 指标 | 结果 | 可解释结论 |
|---|---:|---|
| answered 题 Retrieval Recall@K | 8/8 | 期望 paper/page 均进入实际 RAG 候选 |
| must-cite 条目 Recall | 11/11 | 跨篇题的两个目标均进入候选 |
| Citation completeness | 11/11 | Fake Chat 提交的引用覆盖全部 must-cite 条目 |
| Citation validity | 14/14 | 所有最终输出通过确定性 Validator |
| selected_papers 边界 | 3/3 | 被排除论文没有进入候选或引用 |
| answered 状态匹配 | 8/8 | 有答案题走通 answered 路径 |
| insufficient 状态匹配 | 0/6 | Fake Chat 无语义充分性判断能力 |
| 总状态匹配 | 8/14 | 不能当作真实模型质量分数 |

Fake Chat 的规则是“Prompt 中有 Evidence ID 就回答”，所以无答案题只要检索到任意相关词块便会误报 answered。该失败被如实保留，未针对 Fixture 编写答案脚本。没有运行人工 Groundedness、性能或真实语料质量评测，因此不报告这些指标。

## 可靠性证据矩阵

- API 幂等重放/冲突、busy 与终态 `active_run_id` 自愈：`test_conversation_service.py`、`test_conversations.py`；
- 重复 ARQ Job/Executor 与重复 Evidence/Claim 提交：`test_run_execution_service.py`、`test_evidence_service.py`、`test_rag_answer_executor.py`；
- 临时 Provider 错误进入 RETRY_WAIT，永久错误进入 FAILED：`test_run_execution_service.py`、`test_rag_answer_executor.py`；
- 非法结构修复一次、再次失败稳定 FAILED；检索后及模型调用后取消：`test_rag_answer_executor.py`；
- Message/Claim/Citation/Run/Event 原子提交与 indexing/rag_answer 终态事件：`test_queue_worker.py`、`test_evidence_repository.py`；
- SSE Last-Event-ID、历史重放、终态关闭，以及浏览器端重复 sequence 归并：`test_run_events_stream.py`、`web/src/runs/eventStore.test.ts`；
- 归档限制、owner/Project 隔离、selected scope、Evidence 可见性：`test_conversation_service.py`、`test_chunk_retrieval.py`、`test_evidence_service.py`、`test_conversations.py`。

审计未发现需要复制已有断言的可靠性缺口。Playwright 新增一条 Phase 2 旅程，覆盖导入并等待 ingestion/indexing、Project 问答、RAG SSE、刷新恢复、Citation → Evidence → PDF `#page=N`、单篇 scope 和归档只读；E2E 使用 Fake Provider，不产生费用。

## 真实组件 Smoke

- Docling：`AGENT_RUN_DOCLING_TESTS=1` 的 2 个契约测试通过；本机 CUDA 驱动与当前 PyTorch 不匹配，自动使用 CPU，并产生 Docling 字段弃用告警。首次运行下载约 506 MiB 模型缓存。
- Chat：真实 OpenAI-compatible 请求通过；当前模型不支持 `response_format=json_schema`，需显式 `AGENT_CHAT_JSON_SCHEMA_SUPPORTED=false` 降级为 `json_object`，最终输出仍必须通过本地 `RagAnswerOutput` 与 Citation Validator。
- Embedding：真实 `embedding-3` 请求返回 1 个 1024 维向量且 usage 非空。Base URL 必须是 API 根 `https://open.bigmodel.cn/api/paas/v4`，Adapter 会自行追加 `/embeddings`。
- 测试必须显式设置 `AGENT_RUN_PROVIDER_TESTS=1`；普通测试保持 Fake 且不联网。Key、Prompt 全文、向量和敏感响应均未写入日志或报告。

## 已知限制

- 语料仅 4 篇、33 个 chunks；没有规模或延迟结论，也未验证 HNSW。
- Fake Embedding 只表达词汇重叠，Fake Chat 不判断语义充分性；本报告不证明真实检索/回答质量。
- 真实 Provider 只做一次 Embedding 与一次结构化 Chat 最小请求，没有用真实 Provider 重跑 14 题，也没有宣称生产可用。
- `json_object` 模式依赖 Prompt 明确字段形状，确定性 Pydantic 解析和 Citation Validator 仍是必须边界。

## 60 秒面试说明

“我没有把 Fake 指标包装成模型质量。固定评测复用正式导入、索引、Retriever 和 RAG Executor，在一次性 pgvector 库里跑 14 题；它证明 answered 检索 8/8、引用目标 11/11、scope 3/3 和 Validator 14/14，同时如实暴露 Fake Chat 的 insufficient 只有 0/6。真实 Docling、Embedding 和结构化 Chat 各用显式 opt-in 最小 Smoke 单独验证，普通测试不联网。这样管线正确性、引用完整性和模型质量三种结论不会混在一起。”
