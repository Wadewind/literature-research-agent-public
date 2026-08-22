# Review 论文依赖等待与恢复

## 模块解决的问题

Review Worker 自动导入 arXiv PDF 后，解析和索引由既有 Ingestion/Indexing Run 异步完成。父 Review
Run 不能占住 Worker 轮询，也不能只看子 Run 成功就误认为论文可检索。本模块让父 Run 可靠暂停，按
PaperVersion 的 ready ChunkSet 汇总论文结果，并通过可重置 Outbox 恢复新的 Worker Attempt。

## 边界与执行流程

```text
Review Worker
  → ReviewDependencyWaitService.pause
  → 同事务：Run RUNNING → WAITING_DEPENDENCY
             + dependency_wait_started Event
  → Outbox 保持 DISPATCHED，当前 Attempt → PAUSED

独立 Dependency Reconciler 循环
  → 有界扫描 WAITING_DEPENDENCY Review Run
  → 逐个锁父 Run并二次检查
  → 校验 Source → Paper → PaperVersion → ProjectPaper 范围
  → 观察 Ingestion / ParseRevision / Indexing / ready ChunkSet
  → 保存 Source 与 Dependency 终态 + review_source_* Event
  → 等全部 Source 终态
      ├─ ready 数达到下限：resume_in_session + schedule_again → QUEUED
      └─ 不足：run_failed(no_reviewable_papers/insufficient_reviewable_papers)
```

Reconciler 只读 PostgreSQL 业务事实，不调用 Parser、Embedding、队列执行器或外部 HTTP。Worker 的
lease Reconciler 与依赖 Reconciler 是两个独立循环；一方单轮异常不会停止另一方。

## 状态、数据模型和事务

- `ReviewSource`：`DISCOVERED/IMPORTING → READY | FAILED`，终态不可覆盖；
- `ReviewDependency`：`PENDING → SATISFIED | FAILED`，`satisfied_at` 只在满足时写入；
- `Run`：等待开始为 `RUNNING → WAITING_DEPENDENCY`；正常恢复为
  `WAITING_DEPENDENCY → QUEUED`；全部来源终态但不足时允许
  `WAITING_DEPENDENCY → FAILED`；
- `Outbox`：暂停时保持 `DISPATCHED`；只有正常恢复调用 `schedule_again()` 改为立即到期的
  `PENDING`，不增加 `attempt_count`；业务失败不重投；
- 每轮对账以父 Run 行锁作为同一 Review 的串行化边界。Source/Dependency、Event、父 Run 和
  Outbox 使用同一 session、同一 commit；投递重置失败时全部回滚。

候选扫描本身不宣称认领。多个 Worker 可以扫描到同一 ID，但每个候选在持锁事务中重新确认仍为
`WAITING_DEPENDENCY`，因此只有一个事务能产生最终效果。

## 核心不变量

- Paper 可用于 Evidence 的最终条件是指定 PaperVersion 存在 `READY` ChunkSet，不是 Ingestion 或
  Indexing 的名义成功；
- 父 Run 必须等待全部 Source 成为 `READY/FAILED` 后再固定 Evidence 集，不能因先成功一篇而提前恢复；
- Source 的 Paper/Version 配对、Paper/Version owner、ProjectPaper selected Version 与父 Review 的
  Project/owner 必须一致；
- Slice 3 只为同 Project Ingestion 建 RUN 依赖，所以该依赖还必须匹配 owner、Project 和
  `RunType.INGESTION`；跨 Project Version 复用不创建 RUN 依赖，只观察 Version 的 ready ChunkSet；
- ready ChunkSet 是更强的最终事实；对应 PaperVersion/ChunkSet 依赖必须 satisfied。RUN 依赖仍按
  目标 Ingestion 的真实终态记录，不伪造子 Run 状态；正常数据链中 ready ChunkSet 出现前 Ingestion
  已成功，因此恢复前 RUN 依赖也会同步为 satisfied；
- 零 Source、全部失败以及成功状态缺少下游数据都必须收敛，不能无限等待。

## 关键决定与替代方案

### 为什么等全部来源终态

只要达到最小 ready 数就立即恢复，后完成论文是否进入 Evidence Matrix 会取决于调度速度，导致同一
输入得到不同论文集合。当前方案等待全部来源终态，用更长等待换取确定的 Evidence 边界。默认最小值
仍为 1，它决定“能否继续”，不决定“何时提前继续”。

### 为什么不复用 `apply_run_failure()`

`apply_run_failure()` 面向执行中的 `RUNNING` Run，会读取 Attempt 数、判断重试预算并调用
`reset_for_retry()`。论文全部不可用是依赖汇总后的正常业务终止，父 Run 已处于
`WAITING_DEPENDENCY`，因此直接用合法状态转换和 `run_failed` Event 提交，不消耗失败重试预算。

### 为什么不让父 Worker直接执行子 Run

直接调用 Ingestion Executor 会把两个 Run 的 lease、Attempt、重试和取消语义耦合在一个 Worker
占用中。当前设计只创建/复用子 Run 和依赖，由既有 Outbox/ARQ 执行子 Run，再由数据库对账恢复父
Run，状态所有权更清楚。

## 失败、重试、重复和取消行为

- Ingestion/Indexing 处于 QUEUED、RUNNING、RETRY_WAIT 等活跃状态：本轮保持等待；
- 子 Run FAILED/CANCELLED：Source 和相应 Dependency 使用稳定错误码进入 FAILED；其他来源继续；
- Ingestion 成功却缺当前 Revision、缺 Indexing Run，或 Indexing 成功却缺 ready ChunkSet：作为下游
  数据不变量错误终止该 Source；
- 部分失败：Event 立即持久化并在 commit 后通知，父 Run 仍等待剩余来源；
- 全部终态且至少一篇 ready：正常恢复，不增加 Outbox 失败计数；全部不可用：
  `no_reviewable_papers`；显式更高下限未达到：`insufficient_reviewable_papers`；
- 重复扫描或两个 Reconciler 并发：父行锁与状态条件保证只产生一次 Source Event 和恢复；
- 单个候选出现数据库或不变量异常：该候选事务回滚并记录日志，批处理继续后续候选，避免一个坏 Run
  长期阻塞整批；
- `schedule_again()` 条件失败或抛错：整个事务回滚，下轮仍可重新对账；
- 等待中取消由通用 Run 服务直接转 `CANCELLED`；Reconciler 持锁后若发现状态已改变会跳过，不复活
  已取消 Run。

## 安全和可观测性

对账是内部系统操作，但仍从父 Run 的 owner/Project 出发校验完整关系链。Event 只记录 Source、Paper、
Version、ChunkSet ID、稳定失败码和数量，不保存 PDF、Chunk 文本或 Prompt。Valkey 通知只降低 SSE
延迟，PostgreSQL Event 仍是事实来源。

当前只有结构化日志和业务 Event，尚未为扫描数量、等待时长、来源失败码建立独立 Metrics/Trace；后续
可在 Phase 4 可观测性切片补充。

## 重要测试和运行结果

- 领域：Dependency 单向转换、`WAITING_DEPENDENCY → FAILED`；
- 应用：暂停不重置 Outbox、ready 恢复、部分成功、全部失败、零 Source、重复扫描、中间轮通知、
  Indexing 成功但缺 ChunkSet、跨 Project Version 复用无 RUN 依赖；
- PostgreSQL：Source/Dependency/Event/Run/Outbox 原子提交，两个 Reconciler 并发只恢复一次，
  `schedule_again()` 异常时全部回滚；
- Worker：独立依赖循环调用、取消传播和 ARQ 配置保持不变。

实际结果：定向非集成 `33 passed`，定向 PostgreSQL `3 passed`；后端完整非集成
`457 passed, 4 skipped`，完整 integration `98 passed`；Ruff 与 Pyright 通过。

## 代码入口

- 领域：`backend/src/literature_agent/domain/review.py`、`domain/run.py`
- 应用：`backend/src/literature_agent/application/review_dependency_service.py`
- 恢复组合：`backend/src/literature_agent/application/waiting_run_resume_service.py`
- Port/Adapter：`backend/src/literature_agent/application/ports/review_repository.py`、
  `infrastructure/persistence/review_repository.py`
- Worker：`backend/src/literature_agent/worker.py`
- 测试：`backend/tests/application/test_review_dependency_service.py`、
  `backend/tests/integration/test_review_dependency_reconciler.py`

## 已知限制与扩展路径

- 切片 5 已建立持久 LangGraph Runtime 骨架，但 Evidence 等真实节点尚未完成，因而仍未把 Review
  Executor 注册到生产 Worker；`pause()` 会在后续固定图导入节点接线时调用；
- Reconciler 与 lease 对账复用同一个 30 秒配置间隔和批次大小，真实规模下再基于等待延迟和数据库
  压力决定是否拆分配置；
- 候选扫描未使用 `SKIP LOCKED` 认领；并发正确性由逐 Run 行锁保证，但多 Worker 可能重复扫描候选；
- Run 已提交等待/终态、Attempt 关闭前的 crash gap 已由切片 5 残留 Attempt Reconciler 收敛；
- 当前不会自动清理不再被使用的跨 Project 子 Run或 arXiv 下载缓存。

## 60 秒面试说明

“Review 导入论文后不会占住 Worker 轮询，而是把父 Run 和等待 Event 同事务提交为
WAITING_DEPENDENCY，当前 Attempt 正常 PAUSED。独立 Reconciler 只读取 PostgreSQL，锁定父 Run 后
校验 Paper、Version、Project 和子 Run，最终以 ready ChunkSet 判断论文可检索。它等待所有来源终态
来固定 Evidence 集；至少一篇可用时把依赖结果、父 Run 重新排队、完成 Event 和 Outbox
schedule_again 放在同一事务，全部不可用则稳定失败。并发扫描靠父行锁和条件状态只产生一次效果，
投递重置失败会回滚全部业务变化，因此实现的是可解释的 effectively once，而不是依赖队列状态。”
