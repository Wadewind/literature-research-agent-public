# Evidence 与引用完整性（EvidenceService + Citation Validator）

Phase 2 切片 7 交付领域/持久化能力，切片 8 接入 Run 编排后成文（2026-08-21）。

## 解决的问题

RAG 回答的可信度取决于引用是否真实、可追溯。本模块保证：**回答只能引用本次 Run 实际检索到并固化的 Evidence**，且每条 Evidence 带 paper/version/parse_revision/章节/页码/摘录快照。模型输出只给证据 ID，绑定关系由确定性代码校验，不靠 Prompt 约束。

## 边界与执行流程

```text
Retriever（快照检索）
  → EvidenceService.commit_evidence(run, retrieval_results)
      ├─ 校验每条结果 (paper_id, version_id) ∈ Run input_payload.version_scope 快照
      │    （快照缺失/形状非法/结果在快照外 → EvidenceScopeError，永久错误）
      ├─ parse_revision_id 经 ChunkSetRepository.get_by_id(chunk.chunk_set_id) 解析
      ├─ excerpt 截断 500 字符（EVIDENCE_EXCERPT_MAX_CHARS），不复制 Chunk 全文
      └─ 同一短事务 add_many；(run_id, chunk_id) 唯一约束兜底，重复调用回读幂等
  → ChatModel 生成结构化 JSON（claims[].evidence_ids）
  → validate_citations(output, evidence, run_id)   # domain 纯函数
      → passed / failures（只含稳定 reason code 与 claim 下标，不存文本）
```

## 状态、数据模型和事务

迁移 `c5b8e2f7a3d1` 建四张表：`evidence`（`(run_id, chunk_id)` 唯一；paper/version/parse_revision 为 denormalize 快照列，不建 FK——历史 Evidence 不因移出/换版/归档改变，ADR 0002）、`claim_sets`（`run_id` 唯一——一个 RAG Run 只提交一个 ClaimSet）、`claims`（`(claim_set_id, sequence)` 唯一）、`citations`（复合主键 `(claim_id, evidence_id)`，双 FK）。

Evidence 固化是独立短事务；Claim/ClaimSet/Citation 与 Assistant Message、Run 终态在 RagAnswerExecutor 的最终事务原子提交。

## 关键决定与替代方案

- **Evidence-first**：模型只能引用固化后的 Evidence ID，Validator 校验 ID 存在性（`fabricated_evidence`）与 `run_id` 归属（`cross_run_evidence`），而不是让模型自由生成引用再事后核对；
- **段落级 Claim 严格绑定**：`answered` 时每个 Claim 至少一个 Evidence（`uncited_claim`），`insufficient_evidence` 时 claims 必须为空（`status_mismatch`）；条件一致性无法写进 JSON Schema，由纯函数校验；
- **快照列不建 FK** 的代价是孤儿引用不可被数据库阻止，换来历史可追溯性；正确性由 EvidenceService 的快照校验与检索强过滤链双层保证；
- 结构化输出用 Pydantic v2 严格模型（`extra="forbid"`），解析失败抛 `AnswerOutputParseError`——属可修复的模型输出问题，不注册为 Run 层永久错误，由执行器修复重试一次后仍失败才 FAILED（`model_output_invalid`，永久）。

## 失败、重试、重复和取消行为

- 重复执行：`commit_evidence` 先 `list_by_run` 回读，已固化的 chunk_id 复用既有行；`claim_sets.run_id` 唯一约束兜底重复提交，已有 ClaimSet 时执行器回读幂等完成，不重复创建 Message；
- 校验失败：reason code 全部收集（`empty_claims`/`status_mismatch`/`uncited_claim`/`fabricated_evidence`/`cross_run_evidence`/`duplicate_citation`），失败原因作为反馈消息追加给模型修复重试一次；
- 固化在取消之后发生不阻断：Evidence 行保留，Run 由取消检查推进 CANCELLED，不提交 ClaimSet。

## 安全和可观测性

- 事件 payload 只含计数与 reason code，不含问题/回答文本、证据摘录；
- excerpt 上限 500 字符，日志不记录文本内容；
- Evidence 查询 API（`GET /projects/{pid}/evidence/{eid}`）校验 project 归属，越权统一 404。

## 重要测试和运行结果

- Domain：`test_answer_schema.py`（8 例）、`test_citation_validator.py`（10 例）；
- Application：`test_evidence_service.py`（7 例）、`test_rag_answer_executor.py` 的修复重试/伪造引用用例（13 例文件内）；
- Integration：`test_evidence_repository.py`（5 例，唯一约束与 FK 拒绝）、`test_queue_worker.py::test_rag_answer_completes_end_to_end`（端到端断言 citations 全部指向本次 Run 的 Evidence）；
- 全量：2026-08-21 `pytest tests -q --ignore=tests/integration` 366 passed；`pytest tests/integration -q` 79 passed（含端到端）。

## 代码入口

- `src/literature_agent/domain/answer_schema.py`、`domain/citation_validator.py`、`domain/evidence.py`；
- `src/literature_agent/application/evidence_service.py`；
- 表结构：迁移 `c5b8e2f7a3d1`；适配器 `infrastructure/persistence/evidence_repository.py`、`claim_set_repository.py`。

## 已知限制

- `citations` 无独立查询 API（随消息列表的 Claim 摘要读取）；
- 校验是结构性的（ID 存在、归属正确、无重复），不校验 Claim 文本与 Evidence 内容的语义一致性（Groundedness 由评测人工抽查）；
- 修复重试只有一次。

## 60 秒面试说明

「模型不能自由引用：检索结果先固化为 Evidence 行（带版本、页码、摘录快照），Prompt 里只有证据 ID；模型输出 claims 与 evidence_ids，确定性 Validator 校验每个 Claim 至少绑一个本次 Run 的 Evidence、ID 必须真实存在且属于本 Run，失败把 reason code 反馈给模型修复一次，再失败 Run 终态 FAILED。重复提交靠 `claim_sets.run_id` 唯一约束兜底，回读幂等完成。」
