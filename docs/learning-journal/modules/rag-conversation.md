# RAG Conversation（ConversationService + RagAnswerExecutor）

Phase 2 切片 8 完成后成文（2026-08-21）。

## 解决的问题

切片 6/7 交付了检索与引用完整性能力，但用户还没有可对话的产品形态。本模块交付多轮 RAG 会话：Conversation 归属 Project、固定 scope（整个 Project 或选中论文子集）、提问即创建一个 rag_answer Run，由 Worker 完成「快照检索 → Evidence 固化 → 结构化生成 → 引用校验 → 回答落库」全链路，前端可拿消息列表直接渲染引用。

## 边界与执行流程

```text
POST /conversations/{id}/messages（Idempotency-Key 必填）
  → ConversationService.post_message（一个事务）：
      幂等重放检查 → 归档/busy/not_indexed 校验 → 解析版本范围快照
      → User Message + rag_answer Run（input_payload 固化快照）+ run_created
        + Outbox + 幂等记录
      → try_claim_active_run（WHERE active_run_id IS NULL 并发兜底）
  → 202 {user_message_id, run_id, status: queued}

Worker（RagAnswerExecutor，模型调用全在事务外）：
  事务A 取消检查 + retrieval_started
  → Retriever.retrieve_for_scope（快照过滤，不依赖当前收录关系）
  事务B 取消检查 + retrieval_completed（候选计数）
      零结果 → 直接提交证据不足回答（业务成功路径，不调模型）
  → EvidenceService.commit_evidence（幂等固化）
  事务C 取消检查 + model_generation_started
  → ModelGateway.generate（json_schema）→ 解析 → Citation Validator
      解析/校验失败 → 失败原因作为反馈消息修复重试一次 → 仍失败 FAILED
  事务D model_generation_completed + citation_validation_completed
  最终事务：Assistant Message + ClaimSet + Claims + Citations
      + 清 active_run_id + Run SUCCEEDED + answer_committed 原子提交
```

Route 只做 HTTP 与身份上下文；Domain 保存状态机与不变量；模型调用经 ModelGateway 记录（含 run_id），不发生在数据库事务内。

## 状态、数据模型和事务

迁移 `d7f3a1c9e5b2` 建三张表：

- `conversations`：`active_run_id`（可空 FK → runs）是**单活跃 Run** 认领指针；
- `conversation_scope_papers`：复合主键 `(conversation_id, paper_id)`，`selected_papers` 模式创建时固化；
- `messages`：`(conversation_id, sequence)` 唯一；`run_id` 关联产生它的 Run；`claim_set_id` 关联切片 7 的 ClaimSet。

关键事务边界：提问提交是一个事务（消息、Run、事件、Outbox、幂等记录原子）；回答产物与 Run 终态、清认领在同一事务——用户永远看不到「Run 成功但没有回答消息」的中间态。

## 关键决定与替代方案

- **版本范围快照固化在 Run `input_payload`**（`[{paper_id, version_id}, ...]`）：提问时刻解析，之后移出/换版/归档不影响本次 Run；快照检索 SQL 不 join `project_papers`，只按快照集合 + owner + ready ChunkSet 过滤；
- **busy 语义双层**：服务层预检（active_run_id 指向非终态 Run → 409；指向终态/已消失 → 自愈清理，覆盖 QUEUED 被直接取消等未经执行器的路径）+ SQL 条件更新并发兜底；执行器在**任何终态**（SUCCEEDED/FAILED/CANCELLED）同事务清认领，`RETRY_WAIT` 保留（Run 未结束，会话仍忙）；
- **幂等复用既有 IdempotencyRecord**，不改表结构：`request_hash = sha256(conversation_id:key:sha256(content))`，重放经 `run_id` 回读 User Message，不新建记录；
- **修复重试只有一次**：解析失败或引用校验失败时把失败原因（reason code）追加为反馈消息重新生成一次；不做多轮自我修正；
- **Context Token Budget（2026-08-21 定稿，2026-08-30 调整输出）**：证据上下文沿用检索预算截断结果；模板+证据超 `context_token_budget`（默认 3000，tiktoken 精确计数）按 rank 从低到高丢弃一次，不循环压缩；输出上限 `AGENT_ANSWER_MAX_OUTPUT_TOKENS` 默认 4096；
- **FakeChatModel 证据 ID 驱动**：Prompt 含 `evidence_id=` 标记则确定性返回引用这些 ID 的合法 answered JSON，否则 insufficient——本地闭环与端到端测试不触网。

## 失败、重试、重复和取消行为

- 重复投递：`claim_sets.run_id` 唯一约束兜底，已有 ClaimSet 回读幂等完成，不重复创建 Message；幂等键重放返回相同 run_id；
- 模型临时错误（限流等）→ `RETRY_WAIT`，Outbox 重置待重投，认领保留；永久错误（认证失败、`RagAnswerInputError`、`ModelOutputInvalidError`）→ `FAILED` 并清认领；
- 取消检查分布在事务 A/B/C/D 与最终事务：检索后、模型调用前后命中 `CANCEL_REQUESTED` 即推进 CANCELLED（`run_cancelled`）并清认领，不提交回答产物；
- 集成测试暴露并修复的两个真实 bug：Run 落库时 `event_sequence` 未推进到 2（`run_created` 已占用 1）；同事务写两条事件时内存中的 `event_sequence` 未同步推进——均被 `(run_id, sequence)` 唯一约束在真实 PostgreSQL 上拦下（fake 仓储无约束，曾掩盖）。

## 安全和可观测性

- 所有查询 owner 隔离，越权/不存在统一 404；Retrieval SQL 内强过滤（快照 + `paper_versions.owner_id` + ready ChunkSet）；
- 事件 payload 只含计数、reason code、用量，不含问题/回答文本与证据摘录；
- 每次模型调用（embedding 查询向量 + chat 生成）都落 `model_invocations`（含 run_id、状态、错误分类）；
- 内容长度限制：消息 ≤4000、标题 ≤200、幂等键 ≤255。

## 重要测试和运行结果（2026-08-21 实跑）

- Domain：`test_conversation.py` 7 例；
- Application：`test_conversation_service.py` 24 例、`test_rag_answer_executor.py` 13 例、`test_retriever.py` +2 例；
- API：`test_conversations.py` 14 例；
- Integration：`test_conversation_repository.py` 8 例（含并发 try_claim 双会话恰一个成功）、`test_chunk_retrieval.py` +5 例（移出 Project 后快照仍命中、跨 owner 拒绝）、`test_queue_worker.py` +1 例（ingestion → indexing → rag_answer 三轮派发端到端）；
- 迁移 `d7f3a1c9e5b2` 在一次性 pgvector 容器实跑 `upgrade head → downgrade c5b8e2f7a3d1 → upgrade head` 通过；
- 全量：`pytest tests -q --ignore=tests/integration` 366 passed, 4 skipped；`pytest tests/integration -q` 全部通过；`ruff`/`pyright` 零告警。
- 切片 10 回归：非集成 370 passed、4 skipped，integration 79 passed；可靠性证据逐项审计未发现需复制断言的缺口，完整矩阵见 `rag-evaluation.md`。

## 代码入口

- `src/literature_agent/application/conversation_service.py`、`rag_answer_executor.py`、`retriever.py`（`retrieve_for_scope`）；
- `src/literature_agent/domain/conversation.py`；
- `src/literature_agent/api/conversations.py`；
- `src/literature_agent/worker.py`（dispatcher 注册 `RunType.RAG_ANSWER`，`AGENT_CHAT_BACKEND` 开关）；
- 迁移 `d7f3a1c9e5b2`；适配器 `infrastructure/persistence/conversation_repository.py`、`message_repository.py`。

## 已知限制

- SSE 推送与前端 Conversation UI 已在切片 9 完成，并于切片 10 用 Playwright 固化刷新恢复与引用旅程；
- `project` 模式快照每次提问重新解析（新提问看到最新收录，历史 Run 不变）；
- 无对话级上下文（每次提问独立检索，不带历史消息进 Prompt）；
- Context Budget 超限只按 rank 丢弃，不做摘要压缩。

## 60 秒面试说明

「提问在一个事务里落 User Message、rag_answer Run、事件、Outbox 和幂等记录，并用 `active_run_id` 条件更新保证一个会话同时只有一个回答在跑；Worker 按 Run 固化的版本快照检索——文献之后被移出 Project 也不影响这次回答——固化 Evidence 后让模型只引用证据 ID，引用校验失败修复重试一次；回答、引用、清认领和 Run 终态在同一事务提交，重复执行靠 `claim_sets.run_id` 唯一约束幂等。任何终态都会释放会话，RETRY_WAIT 则保持占用。」
