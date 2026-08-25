# Agent Session 与 Turn 生命周期

## 模块解决的问题

Phase 5 切片 2 把切片 1 的领域契约落到 API、PostgreSQL、Outbox、Worker 和完全离线 Fake Runtime，证明
同一 Project-scoped Session 可以完成两轮交互，同时不让 Runtime Thread/Checkpoint 取代业务事实。

## 执行与事务边界

```text
POST Message 短事务
  → 平台校验 owner / Project / Evidence Matrix / READY ChunkSet
  → 锁 Session 并分配消息 sequence
  → User Message + Run + Snapshots + AgentTurnRun + Events + Outbox + Idempotency
  → 条件认领 active_turn_run_id

RunExecutionService
  → 短事务认领 Run 并创建 Attempt/run_started
  → 事务外 AgentTurnExecutor → Fake Runtime execute/reconcile/collect
  → 新短事务锁 Run，复核 scope 与 RUNNING
  → Bindings + Assistant Message + staged candidate + filtered Events + SUCCEEDED
  → 释放 active Turn
```

Runtime 成功和 PostgreSQL 成功是两个阶段。最终事务发现 Run 已取消、已终态或闭包不一致时，不提交
Assistant Message 或 candidate。

切片 2 的 PostgreSQL Message 负责产品 UI、权限、幂等、审计和受控重建，并未承担模型 Prompt 管理。
当前 `RuntimeTurnRequest` 每轮只传新用户消息和授权/策略快照；切片 4 的真实 Adapter 将复用同一 SDK
Thread，让 `create_deep_agent` 原生 Message、Checkpoint、上下文压缩和文件卸载维护工作上下文，而不是
每轮从产品消息表重放完整历史。

## 数据与不变量

- `agent_sessions.next_message_sequence` 只作为数据库并发分配游标，不进入公开 Domain/API；Repository
  通过 Session 行 `FOR UPDATE` 分配，`(session_id, sequence)` 唯一约束兜底；
- 同 owner 的 Idempotency-Key 保存请求哈希；相同请求回放稳定 Run，不创建第二 Turn，不同请求冲突；
- 每轮 `review_output_id` 必须沿 ReviewOutput/ReviewRun/Run 属于当前 owner/Project，类型和 key 必须是
  `evidence_matrix/evidence-matrix`；
- Project Index Snapshot 只保留当前 Project 中 owner 可见、未归档 Paper 的 selected PaperVersion 与
 最新 READY ChunkSet ID；没有任何 READY ChunkSet 时快速失败；
- Session Binding generation 1 在两轮间复用；每轮有独立 Turn Binding 并引用具体 session binding；
- Binding 重放按 `(session_id, generation)` 精确读取，已有更高 generation 不能改变旧 generation 事实；
- candidate 只保存 owner/project/session/turn、哈希、大小、MIME、名称和受控 content_ref，状态固定
  staged，不写 Review Artifact，也不提供下载；同 Turn/hash 的重复 descriptor 仅在稳定字段完全一致时
  收敛，candidate ID 跨 scope/Turn 碰撞会拒绝并回滚；同一 result 内相同稳定 candidate 先去重，避免
  重复 staged Event 和计数；
- Runtime candidate 在结果事务前经过非空、长度、小写 SHA-256 和 `0..1_000_000` 字节领域校验；
- Turn/User Message/Context/Policy 的闭包由明确命名的循环安全外键约束，不只依赖应用查询。

## 安全与 Event

API 请求不能携带 owner、Thread、Workspace、Policy、MCP、Sandbox 或网络配置。业务 Event 只记录稳定
业务 ID、计数、状态和 candidate 的小型元数据；不保存用户消息、完整 Prompt、Runtime delta、思考过程、
Matrix/Chunk 正文、Secret 或 candidate 文件正文。
纯空白消息由请求 Schema 直接拒绝，Idempotency-Key 缺失、纯空白或超长在 HTTP/Application 边界分别
返回稳定错误或业务校验错误，不进入写事务。

## 测试与已知限制

真实 PostgreSQL 两轮测试覆盖四条严格递增 Message、两个 Run/Attempt/Event、同一 Session Binding、两个
Turn Binding、Snapshot 固化、staged candidate、幂等回放、generation 精确读取、candidate 冲突、非法
descriptor 回滚和 owner 隔离。Application Service、Executor 与 Repository 各有独立行为测试，不以
import smoke 冒充分层覆盖；迁移在临时 PostgreSQL 18 上完成 upgrade/downgrade/upgrade/check。

当前 Fake 状态仍在单 Worker 进程内；响应丢失、重复 Job、崩溃、取消竞争与 reconcile 故障注入属于
切片 3。正式 Project Retriever/Matrix Reader/Citation Validator 属于切片 5。Deep Agents、MCP、
Browser、Sandbox、WorkspaceSnapshot 和 Skills 均未接入。

因此本切片证明的是业务事实和事务闭环可以保留，并不证明平台已经实现或取代 Deep Agents 的消息管理；
原生两轮 Thread、Checkpoint 和 summarization 必须在切片 4 使用 Fake Chat Model 独立验证。

## 代码入口

- `application/agent_session_service.py`
- `application/agent_turn_executor.py`
- `application/ports/agent_repository.py`
- `infrastructure/persistence/agent_repository.py`
- `api/agent_sessions.py`
- `worker.py`

## 60 秒面试说明

我把一次 Agent 用户消息建模成通用 Run 的一对一扩展，在短事务里原子保存消息、授权快照、事件、Outbox
和幂等事实，再由 Worker 事务外调用 Runtime。Runtime 成功后开启第二个短事务，重新锁定并校验 Run 和
owner/Project/Session/Turn 闭包，才保存稳定 Binding、助手消息和 staged candidate。这样同一 Session 的
两轮可以复用 Thread 语义，同时 PostgreSQL 仍掌握状态、权限和可恢复的对话历史；Runtime 成功不会被
错误当成业务提交成功。这里的产品消息历史不用于每轮重建 Prompt；真实 Adapter 将只追加新消息，并由
`create_deep_agent` 原生维护 Runtime 上下文。
