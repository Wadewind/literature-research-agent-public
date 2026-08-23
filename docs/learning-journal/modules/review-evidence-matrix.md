# Review Evidence Matrix

## 模块解决的问题

综述不能把所有论文全文一次性交给模型后直接生成最终文章。本模块先把每篇论文按稳定分析维度提取为
结构化 Matrix，并将每条结论绑定到当前 Review Run 中真实持久化的 Evidence。它解决长短论文上下文
规划、模型输出不可信、引用跨范围和至少一次执行重复副作用四类问题。

## 边界与执行流程

```text
RUNNING Review Run + scoped search-strategy.v1 Output
  → 校验 3–6 维与 READY Source/PaperVersion/Revision/ChunkSet
  → 短论文：全部 Chunk；长论文：每维 exact-version Retriever top 5
  → 合并、去重、文档顺序、token 限额
  → 先固化 Review Run Evidence
  → 每篇一次正常模型调用提取全部维度
  → 确定性 Validator
       └─ 首次非法：保留相同受控上下文，最多结构修复一次
  → 单篇成功或永久失败 Output
  → 聚合 Output + Step 成功 + completed Event 同事务
```

服务本身不创建 Search Strategy，也不生成 Outline/Section/Artifact。切片 9 已由生产
`ReviewExecutor` 在图外依次调用固定 Search Strategy、arXiv 与本服务，再进入 Outline→Artifact
持久图；Matrix 不再作为图的 `END` 或成功边界。

Phase 4 切片 4 已通过 Project-scoped Review API 在前端展示聚合 Matrix。页面保留
`insufficient_evidence` 行，并仅把持久 `evidence_id` 交给现有 Evidence API 获取 Version、页码和摘录；
Matrix Output 本身不携带 Storage 路径，也不成为 PDF 授权来源。

## 状态、数据与事务

- 分析维度从当前 owner/Project/Review Run 的 `SEARCH_STRATEGY` ReviewOutput 加载，Schema 固定为
  `search-strategy.v1`，不信任节点调用方临时参数；
- Prompt 固定为 `review-evidence-extraction.v1`；新建 Review Run 快照记录该版本；
- Matrix 行只保存 `paper_id/dimension_key/status/finding/limitations/evidence_ids`。PaperVersion、
  ParseRevision、Chunk、页码与章节可通过 Evidence 回查；
- 每个合法 Source 必须指向同 owner 的 PaperVersion、成功 Revision、READY ChunkSet 与非空 Chunk；
  READY Source 不得重复 Paper；
- Evidence 在模型调用前以 `(run_id, chunk_id)` 幂等固化；单篇及聚合 Output 分别使用稳定幂等键；
- 总 `BUILD_EVIDENCE_MATRIX` Step 使用条件状态更新，避免旧执行者把终态回写为 RUNNING；最终聚合
  Output、Step 的 SUCCEEDED 和 `evidence_matrix_completed` Event 在锁定 Run 的短事务中提交。

外部 Retriever 和模型调用均不在数据库事务中。Evidence、单篇成功或永久失败 Output 都先提交，
之后崩溃可由稳定幂等键回放；即使后续论文发生临时错误而未形成聚合 Output，重试也不会再次调用已
永久失败论文。聚合 Output 提交后、checkpoint 前崩溃时，下一次调用先验证聚合闭包并直接返回。

## 关键决定与替代方案

- 短论文以估算总量 12,000 tokens 为界，按序使用全部 Chunk；长论文按每个维度复用 Phase 2
  `retrieve_for_scope()`，传入唯一 `(paper_id, version_id)`，再把每维 top 5 合并到 16,000 tokens。
  没有选择“整篇 × 每维调用”或“每维独立生成”，避免重复全文成本，并固定每篇一次正常生成；非法
  输出才允许最多一次额外修复；
- 先持久化 Evidence，再构造 Prompt。未采用 Prompt 临时 Chunk ID，否则 Matrix 结果无法被后续章节
  和 Citation Validator 稳定回查；
- `finding`/`limitations` 各 500 字符、每行最多 10 个 Evidence，聚合载荷最多 240 KiB。这在
  10 篇 × 6 维的 Profile 上限下仍低于 ReviewOutput 的 256 KiB 领域边界；
- 当前领域只有一个固定 `BUILD_EVIDENCE_MATRIX` Step，没有为每篇论文制造假 Step key。单篇永久失败
  先写入稳定 per-paper 失败 Output，并在聚合时投影到 `paper_failures`；未来若产品需要逐论文时间线，
  应先扩充明确的数据契约。

## Validator 与失败行为

Validator 不依赖 Prompt 自觉，确定性校验：严格顶层/行字段、字段类型、完整且不重复的维度集合、
Paper ID、状态组合、文本与引用数量，以及 Evidence 的 Run/Project/Paper/Version 范围。
`extracted` 必须有非空 finding 和至少一个 Evidence；`insufficient_evidence` 必须将 finding、
limitations 和 evidence_ids 全部清空。

JSON、Schema 或引用首次非法时，只在原提取消息后附加截断的原输出和结构化错误，保留完全相同的允许
证据，再调用一次 repair。第二次仍非法则以 `evidence_matrix_invalid` 记录该论文；有其他有效论文时
继续，全部论文无效时抛永久错误并将总 Step 置为 FAILED。超大模型输出使用稳定
`output_too_large` issue；聚合超限使用 `evidence_matrix_too_large`。

## 重试、重复与取消

- Scope/Matrix 无效属于永久错误，Provider/数据库临时错误继续交给既有执行失败策略；
- 并发 Evidence 和 Output 写使用 PostgreSQL 唯一约束与 `ON CONFLICT DO NOTHING` 收敛；回读后
  仍比较稳定语义，防止同一幂等键掩盖不同业务结果；
- 聚合前重试会复用单篇成功与永久失败 Output；聚合结果存在时不会重跑任何论文，也不会重复
  completed Event；
- 本服务只在 RUNNING Run 执行，并在最终提交的 Run 行锁内复核状态。协作取消的图节点安全点要在
  生产 Executor 接线时实现，本切片没有越过切片 7 提前构建取消编排。

## 安全与可观察性

模型输入只包含研究问题、受控维度、白名单 Paper 元数据和被选 Evidence/Chunk context；Source
元数据中的未知字段不会进入 Prompt。Validator 与写服务均要求 owner、Project、Run、Paper 和 Version
闭包。Event 只记录 Output ID 和成功/失败计数，不记录全文、Prompt 或模型原输出。

可观察性以一个总 Step、单篇/聚合 ReviewOutput、聚合 `paper_failures` 与
`evidence_matrix_completed` Event 提供；普通日志不得记录论文全文。

## 重要测试和运行结果

- 领域：合法 extracted/证据不足；未知/缺失/错误类型；重复维度/引用；伪造及跨 Run/Project/
  Version Evidence；文本边界；10 篇 × 6 维最坏合法载荷预算；
- 应用：短论文全文顺序、长论文每维 exact-version retrieval/合并顺序、Prompt 白名单、一次 repair、
  部分/全部失败、已有 Evidence 语义冲突、最终 Output crash replay、Run 与 Search Strategy 范围；
- PostgreSQL：Evidence/Output 并发收敛、跨 owner 隔离、外层事务回滚、Step 终态不回退。

最终完整回归数字见 Phase 3 §18.6。

## 代码入口

- 领域结构与 Validator：`backend/src/literature_agent/domain/review_evidence_matrix.py`
- 应用编排：`backend/src/literature_agent/application/review_evidence_matrix_service.py`
- Phase 2 Retriever：`backend/src/literature_agent/application/retriever.py`
- Evidence/Output PostgreSQL 幂等写：
  `backend/src/literature_agent/infrastructure/persistence/evidence_repository.py`、
  `backend/src/literature_agent/infrastructure/persistence/review_repository.py`
- 测试：`backend/tests/domain/test_review_evidence_matrix.py`、
  `backend/tests/application/test_review_evidence_matrix_service.py`、
  `backend/tests/integration/test_review_evidence_matrix_idempotency.py`

## 已知限制

- 生产 Review Executor 已在切片 9 接线：Matrix 完成事务同时保存聚合 Output、成功 Step、Event，并把
  Stage 推进到 `PROPOSE_OUTLINE`，随后进入 Outline interrupt；
- `estimated tokens` 沿用 Chunk 的 `token_count`，阈值、top K 和上下文预算仍需 Fake/真实小样本校准；
- Validator 验证结构与引用闭包，不自动证明 finding 在语义上被 Evidence 完全蕴含；
- 当前 per-paper 失败有独立 ReviewOutput，但没有独立 per-paper Step/Event；
- 模型调用后的取消安全点、Usage 聚合与 API/SSE 已由生产执行闭环补齐；Provider 返回后、Output 提交
  前崩溃仍可能重复外部模型调用，但持久 Output/Event 通过稳定键收敛。

## 60 秒面试说明

“我没有让模型直接拿所有论文写综述，而是先做 Evidence Matrix。短论文给按序全文，长论文对每个分析
维度用 Phase 2 Retriever 在精确 PaperVersion 范围内检索，合并成一次每篇论文调用。Chunk 会在调用
模型前固化成当前 Review Run 的 Evidence，所以模型只能输出真实、后续可回查的 evidence_id。一个
确定性 Validator 再检查 Schema、完整维度、状态组合和 Run/Project/Paper/Version 闭包；失败只修复
一次。Evidence 和 Output 用唯一键收敛并发，最终 Output、Step 和 Event 同事务，checkpoint 前崩溃
重放不会重复模型或 Event。生产 Executor 只把依赖等待留在图外，Matrix 完成后进入唯一的 Outline
interrupt，因此等待释放 Worker 与 LangGraph HITL 的语义不会混在一起。”
