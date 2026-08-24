# 可靠投递与恢复模块（Queue Outbox + ARQ Worker + Attempt/lease）

## 解决的问题

创建 Run 的数据库事务提交后，需要把执行请求可靠地交给后台 Worker。如果 API 进程在提交后、投递前崩溃，或者 Valkey 短暂不可用，任务不能丢失；如果投递或 Job 执行发生重复，也不能产生重复执行。Worker 崩溃或临时错误也不能让 Run 永久卡在 RUNNING。本模块用持久化 Queue Outbox + 幂等 Worker 执行实现至少一次投递、业务上 Effectively Once；切片 8 起加入 Attempt/lease/heartbeat 与对账循环，覆盖执行期间的崩溃恢复与错误分类重试。

## 边界与执行流程

```text
IngestionService.upload_paper_file
  → 同一事务：Run + run_created Event + QueueOutbox(pending)
                                              │
Worker 进程（python -m literature_agent.worker）│
  ├─ Outbox 派发循环（OutboxDispatchService，周期轮询）
  │    → list_due_pending → 投递前检查 Run 状态
  │       （RETRY_WAIT 条件转回 QUEUED + run_requeued Event；终态/取消中直接丢弃）
  │    → ARQ enqueue_job(run_id, _job_id="run:<run_id>")
  │    → try_mark_dispatched（条件更新，独立短事务）
  │    → 失败：attempt_count + 1，指数退避，达上限进入 failed
  ├─ 对账循环（RunReconcileService，周期 30s）
  │    → 收回 lease 过期（600s 无心跳）的 RUNNING Run
  │    → 关闭旧 Attempt（worker_crashed）→ 按失败策略重投或 FAILED
  ├─ Review 依赖对账循环（ReviewDependencyReconciler，周期 30s）
  │    → 汇总 PaperVersion 的 ready ChunkSet/终态失败
  │    → 同事务恢复父 Run + Event + schedule_again，或以稳定业务错误终止
  └─ ARQ Job execute_run(run_id)
       → RunExecutionService.execute
            → 条件认领 QUEUED → RUNNING，同事务创建 Attempt（+ run_started Event）
            → 执行期间心跳任务周期更新 Attempt.heartbeat_at（30s，失败只记日志）
            → 事务外调用执行器（IngestionExecutor + Parser 组合）
            → 执行器负责推进 RUNNING → 终态/重试/等待状态
            → 执行器退出后按 Run 状态 best-effort 关闭 Attempt

正常等待恢复（Phase 3 切片 1）
  → WaitingRunResumeService
    → 同一事务：WAITING_* → QUEUED
                 + 原因 Event
                 + Outbox DISPATCHED → PENDING（schedule_again）
  → Dispatcher 投递新 Job → 新 Attempt
```

- 数据库提交与队列投递之间不假设原子性：Outbox 是持久化间隙，崩溃后下一轮派发循环补投；
- 外部队列调用发生在数据库事务外，每条 Outbox 记录的标记独立提交；
- Worker 从 PostgreSQL 读取事实，ARQ Job 只携带 `run_id`，ARQ Result 不作业务事实。

## 状态、数据模型和事务

- `QueueOutbox`：`outbox_id`、`run_id`（唯一）、`status`（`pending`/`dispatched`/`failed`）、`attempt_count`、`scheduled_at`、`dispatched_at`、`created_at`、`updated_at`。
- `run_id` 唯一约束保证一个 Run 最多一条投递记录。
- 投递失败退避：`min(2^(n-1), 60s)`；达到 `outbox_max_attempts`（默认 10）进入 `failed` 终态，等待人工介入。
- `try_mark_dispatched` 是 `WHERE status='pending'` 的条件更新，重复标记只有一个生效。
- `run_attempts` 表：`attempt_id`（PK）、`run_id`（FK）、`attempt_number`、`worker_id`、`status`（`running`/`paused`/`succeeded`/`failed`/`cancelled`）、`started_at`、`heartbeat_at`、`finished_at`、`error`（JSONB）；唯一约束 `(run_id, attempt_number)`。Attempt 是运维记录（lease/对账依据），业务事实仍以 Run 和 Event 为准。
- 重试复用 Outbox 行：`reset_for_retry` 把 `dispatched` 条件重置为 `pending`、推迟 `scheduled_at` 并累计 `attempt_count`，不新增行。
- 正常恢复也复用同一 Outbox 行：`schedule_again` 把 `dispatched` 条件重置为立即到期的
  `pending`，清空 `dispatched_at`，但不增加 `attempt_count`；它不表示失败重试。

## 关键决定与替代方案

- **Outbox 而不是 API 直接投递**：API 直接 enqueue 会在“DB 已提交、队列未投递”的崩溃窗口丢任务；Outbox 把投递变成可重放的后台动作。
- **run_type 显式分发**（Phase 2 切片 4）：Worker 装配 `RunDispatcher` 组合执行器（`application/run_dispatcher.py`），按 `run.run_type` 分发到 ingestion/indexing 执行器；未知或未接线类型把 Run 推进 FAILED（错误类型 `unknown_run_type`），不静默执行。`RunExecutionService` 的单 executor 签名不变，dispatcher 作为组合 executor 注入；各执行器另有 run_type 防御。
- **indexing Run 的触发**（Phase 2 切片 4）：IngestionExecutor 结果提交事务内（含复用路径）同时创建 indexing Run + `run_created` + Outbox，保证解析成功必然跟随索引，不引入独立扫描循环。
- **ARQ Job ID 去重**：`_job_id = "run:<run_id>"`，队列内同一 Run 同一时间只有一个待执行/执行中 Job，Outbox 补投天然去重。`execute_run` 以
  `func(..., keep_result=0)` 注册；ARQ Result 不是业务事实，完成后立即释放 result/job key，使同一业务
  Run 从 Dependency/HITL 恢复时能用相同稳定 ID 创建合法新 Attempt。并发重复仍由执行中的 Job key
  去重，业务单效果仍由 PostgreSQL Run 条件认领保证。
- **Worker 端幂等执行**：`RunExecutionService` 只认领 `QUEUED` 的 Run（条件更新），重复 Job、已终态、已取消一律跳过。
- **`max_tries = 1`**：ARQ 不叠加自动重试，重试只有 Outbox 退避一层主导，避免多层重试相乘。
- **派发器在 Worker 进程内**：首版不单建 Dispatcher 部署单元；多实例并发派发时由 Job ID 去重和条件更新兜底。
- **API 侧队列连接惰性建立**：lifespan 注入 `ArqRunQueue`，连接池首次投递时才创建，API 启动不强依赖 Valkey 可用。
- Phase 4 切片 5 后，Outbox 成功/失败和 Worker 三个周期循环使用固定 JSON event。ARQ Job 仍只携带
  `run_id`；Worker 以 job_id/run_id 的摘要生成本地 correlation，认领成功后从 PostgreSQL 绑定
  project/run type/attempt。重复 Job 记录 `run_execution_skipped`，不会把 API contextvar 当成跨进程事实。
- Phase 4 切片 6 的 Outbox/Run/Attempt/Worker active 指标只表达当前 Worker 进程的执行尝试。重复投递
  或恢复会增加计数，进程重启会归零；它们不替代 PostgreSQL 中的 Outbox、Run、Event 与 Attempt。
- **重投复用 Outbox 行**（切片 8 决策）：不给 Run 加重试字段，RETRY_WAIT 的唤醒时间用 `outbox.scheduled_at` 表达；代价是 Outbox 语义从"首次投递"扩展为"投递/重投记录"。
- **错误分类最小两类**（2026-08-20 定稿）：永久错误（`InvalidPdfInputError`/`FileValidationError` 输入类）直接 FAILED；临时错误（`parser_timeout`、资源类、未知异常）预算内 RETRY_WAIT 重试，预算 `max_run_attempts` 默认 3（含首次），退避沿用 Outbox 参数。分类表集中在领域函数 `is_permanent_error`。
- **Outbox 不可重置时降级 FAILED**：重试前提是 Outbox 记录仍可重置；记录缺失或状态异常时无法保证重新投递，直接 FAILED，避免 Run 滞留 RETRY_WAIT。
- **对账循环放在 Worker 进程**：不单独部署 Reconciler；多实例并发对账由持锁二次校验 + 条件更新兜底。
- **Attempt 关闭是 best effort，并由对账兜底**：不要求 Attempt 与 Run 等待/终态同事务。切片 5
  增加残留 Attempt 对账：保护当前最新合法 Attempt，并按其时间区间内的持久 Event 区分正常暂停与
  失败重试；条件关闭和 Run 行锁保证重复/并发单效果。
- **PAUSED 表示正常释放 Worker**（Phase 3 切片 1）：Run 进入等待输入或依赖后，当前 Attempt
  以 `paused` 结束，不进入失败预算；Outbox 保持 `dispatched`，直到正常恢复事务调用
  `schedule_again`。
- **正常恢复不是 Dispatcher 的隐式行为**：受限应用服务显式提交 `WAITING_* → QUEUED`、原因
  Event 和 Outbox 重置，避免仅修改队列状态而丢失业务时间线。

## 失败、重试、重复和取消行为

- 队列不可用：投递抛错 → `attempt_count + 1`、按退避推迟，记录保持 `pending`，恢复后补投（有集成测试）。
- 投递成功但标记前崩溃：记录保持 `pending`，下一轮重复投递，队列去重 + Worker 幂等保证 Effectively Once。
- 执行体抛错（切片 8 起按错误分类）：永久错误 → FAILED + `run_failed`；临时错误且预算内 → RETRY_WAIT + `run_retry_scheduled` + Outbox 重置，派发循环到点重投；预算耗尽 → FAILED。Event 只记录错误类型和截断消息，不记录堆栈。
- Worker 在 Run 仍为 RUNNING 时崩溃：Attempt 停止心跳，lease 过期（600s）后对账循环收回——
  Attempt 置 `failed`（worker_crashed），Run 按失败策略 RETRY_WAIT 重投或 FAILED。
- Worker 在 `CANCEL_REQUESTED` 后崩溃：心跳新鲜时继续等待协作取消；最新 Attempt lease 过期后，
  对账将 Attempt/Run 收敛为 `cancelled` 并写 Event，不重置 Outbox、不进入失败预算。
- 复活的原 Worker 提交结果时条件更新失败（Run 已非 RUNNING 或已被收回），不产生第二个终态。
- 执行期间并发取消：完成时条件更新失败，返回 SKIPPED，不产生第二个终态。
- 正常恢复重复调用：第一次将 Run 改为 QUEUED 后，后续调用被条件状态拒绝，不重复写 Event；
  Outbox 缺失或状态异常时整个事务回滚，不留下已排队却无法投递的 Run。

## 安全和可观测性

- Job Payload 只有 `run_id`；Event Payload 不保存 PDF、全文或堆栈。
- 派发失败和执行失败通过 `logging` 记录 `run_id`/`outbox_id` 与异常信息。
- 配置项：`AGENT_REDIS_URL`、`AGENT_OUTBOX_POLL_INTERVAL_SECONDS`、`AGENT_OUTBOX_MAX_ATTEMPTS`、`AGENT_OUTBOX_DISPATCH_BATCH_SIZE`、`AGENT_WORKER_LEASE_SECONDS`（600）、`AGENT_WORKER_HEARTBEAT_INTERVAL_SECONDS`（30）、`AGENT_WORKER_RECONCILE_INTERVAL_SECONDS`（30）、`AGENT_MAX_RUN_ATTEMPTS`（3）。

## 重要测试和运行结果

- `tests/domain/test_queue_outbox.py`：创建默认值、标记投递、失败退避、终态、退避上限。
- `tests/application/test_outbox_dispatch_service.py`：派发成功、未到期跳过、失败退避、上限进入 FAILED、崩溃后补投安全。
- `tests/application/test_run_execution_service.py`：QUEUED → SUCCEEDED、重复 Job 跳过、缺失/已取消跳过、执行失败进入 FAILED、并发只有一个完成。
- `tests/integration/test_outbox_repository.py`：PostgreSQL 唯一约束、外键、到期查询、`try_mark_dispatched` 条件更新。
- `tests/integration/test_queue_worker.py`：Valkey + ARQ + PostgreSQL 端到端——Outbox → ARQ → Worker → Run SUCCEEDED；队列故障后恢复补投；相同 Job ID 执行中去重；首次 Attempt PAUSED 后用同一稳定 Job ID 恢复并创建第二 Attempt；两个不同物理 Job ID 重复携带同一 `run_id` 时仍只有一个业务效果；Attempt 按 paused/succeeded 关闭。
- `tests/domain/test_run_attempt.py`、`test_retry_policy.py`：Attempt 生命周期、永久/临时分类、退避上限。
- `tests/application/test_run_reconcile_service.py`：lease 过期收回重投、心跳新鲜不动、终态跳过、预算耗尽 FAILED。
- `tests/integration/test_attempt_repository.py`：唯一约束、心跳/结束条件更新、过期查询 join Run 状态。
- `tests/application/test_waiting_run_resume_service.py`：依赖/Human Input 恢复、状态约束、重复调用与所有权。
- `tests/integration/test_waiting_run_resume_transaction.py`：正常恢复三项效果同事务及 Outbox 异常回滚。
- `tests/integration/test_outbox_repository.py`：`schedule_again` 不增加计数，`reset_for_retry` 增加计数。

切片 8 完成时的历史快照：`uv run pytest -q` 148 passed + 2 skipped，`ruff check` 与 `pyright` 无告警。当前测试基线以 Phase 1 进度记录为准。

Phase 3 切片 1 验证：Backend 非集成 `387 passed, 4 skipped`，完整 integration
`86 passed`；`ruff check src tests` 与 `pyright` 通过。新增的 7 个定向 PostgreSQL 用例覆盖
`PAUSED`、等待状态活跃性、`schedule_again` 计数差异及恢复事务回滚。

## 代码入口

- 领域：`backend/src/literature_agent/domain/queue_outbox.py`
- 端口：`backend/src/literature_agent/application/ports/outbox_repository.py`、`run_queue.py`
- 服务：`backend/src/literature_agent/application/outbox_dispatch_service.py`、`run_execution_service.py`、`run_reconcile_service.py`、`failure_policy.py`
- 正常恢复服务：`backend/src/literature_agent/application/waiting_run_resume_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/persistence/outbox_repository.py`、`infrastructure/queue/arq_run_queue.py`
- Worker 入口：`backend/src/literature_agent/worker.py`
- 迁移：`backend/migrations/versions/a4e0bc996e3b_create_queue_outbox_table.py`、`28a3aeb62280_create_run_attempts_table.py`
- 部署：`deploy/compose/compose.yml`（valkey、worker）、`backend/Dockerfile`

## 已知限制

- 执行体已接入真实 Docling + pypdf 降级组合（切片 7，见 document-parsing 笔记）。
- 单实例派发与对账循环；多实例依赖 Job ID 去重和条件更新，未使用 SKIP LOCKED 认领。
- ARQ 结果已按函数禁用保留，不能使用 ARQ Result 查询业务结果；这与既有“PostgreSQL 是事实来源”一致。
- Outbox `failed` 终态（投递层）与 Run `failed`（预算耗尽）暂无自动告警和人工重放入口。
- `schedule_again` 已由 Review Dependency Reconciler 与 HumanInput 服务在各自正常恢复事务中调用；
- Run 已提交等待/终态后、Attempt best-effort 关闭前的崩溃已由 Phase 3 切片 5 补偿；无法从业务
  Event 确定旧 Attempt 原因时会安全保留并等待诊断，不进行猜测性关闭。
- 协作式取消不保证立即终止已进入 Parser 的底层计算。
- SSE 实时通知在切片 9 接入（见 run-event 笔记）。

## 60 秒面试说明

“可靠投递模块用一条可重置 Outbox 跨越数据库提交与 ARQ 投递间隙。失败重试通过
`reset_for_retry` 累计计数和退避；等待输入或依赖时，Attempt 以 `PAUSED` 正常释放 Worker，
恢复则通过 `schedule_again` 立即重投且不占失败预算。恢复时 Run、原因 Event 和 Outbox 在同一
事务更新；执行侧再用稳定 Job ID、Run 条件认领、Attempt lease 和对账收回处理重复投递，以及
Run 仍为 RUNNING 时的 Worker 崩溃，实现业务上的 Effectively Once。等待/终态提交后的 Attempt
关闭崩溃间隙由残留 Attempt Reconciler 根据持久 Event 收敛。”
