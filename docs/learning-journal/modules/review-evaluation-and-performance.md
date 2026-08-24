# Review 固定评测与本机性能基线

## 模块解决的问题

本模块把 Phase 2 固定语料、Review 分层测试和一次本机测量收束成可重复证据，避免把“单元测试通过”、
“Fake 结构合法”和“真实模型质量”混成一个漂亮分数。质量门阻断结构回归；性能数据只描述本机观察，
不承诺 SLA。

## 边界与流程

```text
Phase 2 manifest + 4 合成 PDF
  → 正式导入/解析/索引/Retriever/RAG/Citation runner
  → 3 个固定 Review 问题和实际 3–4 篇语料 ID
  → 生产 Matrix/Citation/Section Validator + 确定性导出质量场景
  → 固定 12 节点 Application/LangGraph/PG 组合回归
  → 存活端点、正式 RAG 管线、完整 Fake Review API/Worker/HITL 的独立本机测量
```

质量场景不是第二套 Workflow，也不是浏览器黑盒 E2E。它实际消费 `review_manifest.json` 的问题和
Phase 2 语料，调用生产 Domain Validator/导出器，并从 Source、Matrix 行、Project/Run scope probe 和
引用映射计数。Owner 隔离需要 Project-scoped Application/PG 才能证明，因此和 partial source、feedback
interrupt/resume、持久化、终态、重放一样由固定 12 节点组合回归证明，明确不折算成领域质量比例。
浏览器完整旅程属于切片 9。

## 阈值和失败行为

首次成功实跑的五项领域结构指标均为 1.0，因此阻断阈值固定为 100%。任何适用场景失败都直接使整个场景
失败；没有可通过其他场景平均掉的权重。空评测或某结构门没有适用场景也失败。这个阈值只针对确定性
结构闭包，不用于语义 Groundedness、Coverage、Redundancy 或真实 Provider 波动。

实际保留两个失败样例：受限沙箱第一次无法访问 Docker socket，checkpoint 场景是“未执行”而不是
产品通过；给 runner 增加数据库规模字段时首次误写表名 `elements`，正式管线在报告阶段失败，修正为
真实表 `document_elements` 后才接受基线。RAG 的 6 个 insufficient 题仍为 0/6，未修改 Fake Chat
迎合答案。

## 性能测量

详细环境、命令和数值见[本机性能基线](../reports/phase-04-performance-baseline.md)。TestClient 只测
`/health/live` 存活端点开销；RAG 使用冷一次性 pgvector/Storage；Worker RSS 使用真实
PostgreSQL+Valkey+ARQ ingestion/indexing/RAG 集成路径，并另用正式 API+PG+Valkey/ARQ Worker+Runtime
跑完 4 Sources Review。完整旅程 wall 5.372 秒，其中脚本自动 HITL pause 1 秒，active 4.371 秒；Worker
RSS/VmHWM 133,220 KiB。终态为 succeeded/finalize，3 ready + 1 failed，13 Steps 全成功、两轮 HITL、
22 Events、6 个可读 Artifact。分层 Domain Validator/导出三场景 0.970 ms 继续仅作为结构质量门耗时，
不替代完整旅程。当前 8 Chunk
规模下精确 pgvector Retrieval p95 约 14.9 ms，没有证据表明精确检索是瓶颈，不提出 ANN。

## 真实 Provider

本切片没有显式凭证，也没有读取 `.env` 或发出真实请求。Phase 2 已有 2026-08-21 Docling、Embedding
和 Chat 最小 Smoke 证据；它缺少完整 Review、固定 token/耗时明细，不能冒充本次真实 Review 质量报告。
复现边界和需补字段见[真实 Provider 评测记录](../reports/phase-04-real-provider-evaluation.md)。

## 代码入口和测试

- `backend/tests/evaluation/run_phase2_eval.py`
- `backend/tests/evaluation/review_manifest.json`
- `backend/tests/evaluation/review_metrics.py`
- `backend/tests/evaluation/run_phase4_review_eval.py`
- `backend/tests/performance/run_phase4_api_baseline.py`
- `backend/tests/performance/run_phase4_review_baseline.py`
- `backend/tests/evaluation/test_review_metrics.py`

## 已知限制

- 分层质量场景不含 PG/Worker；完整 Review 性能由独立正式旅程给出，不把两种口径混为一个数字；
- TestClient 不含 TCP；完整 Review Worker RSS 来自新启动独立 Worker 的 procfs，但不含容器内存；
- Fake 模型不判断语义充分性；真实 Provider 尚未在本切片重跑；
- 运行次数为一轮，未做统计显著性、容量上限或并发压测。

## 60 秒面试说明

“我把评测分成三层。Phase 2 的 4 篇 14 题继续走正式导入、pgvector Retrieval、RAG 和 Citation；Phase 4
的 3 个问题实际进入生产 Validator/导出器，范围、映射和伪造拒绝从事实计数，终态、重放和 HITL 则
保留为固定 12 节点组合回归，不混成质量分数。性能记录冷库 Retrieval/RAG、存活端点开销和真实 ARQ
Worker 路径 RSS；完整 Review 则以正式 API、两轮自动 HITL、Artifact 读取和 Worker RSS 单独实测。第三
次旅程发现 ARQ Result 阻塞等待恢复，修复并以真实 PG/Valkey 3-test 回归后，第四次才接受基线。Fake 的 insufficient
仍然 0/6，真实 Provider 本切片没凭证就不重跑、不读 .env，也不伪造报告。”
