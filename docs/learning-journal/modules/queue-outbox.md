# 可靠投递模块（Queue Outbox + ARQ Worker）

## 解决的问题

创建 Run 的数据库事务提交后，需要把执行请求可靠地交给后台 Worker。如果 API 进程在提交后、投递前崩溃，或者 Valkey 短暂不可用，任务不能丢失；如果投递或 Job 执行发生重复，也不能产生重复执行。本模块用持久化 Queue Outbox + 幂等 Worker 执行实现至少一次投递、业务上 Effectively Once。

## 边界与执行流程

```text
IngestionService.upload_paper_file
  → 同一事务：Run + run_created Event + QueueOutbox(pending)
                                              │
Worker 进程（python -m literature_agent.worker）│
  ├─ Outbox 派发循环（OutboxDispatchService，周期轮询）
  │    → list_due_pending → ARQ enqueue_job(run_id, _job_id="run:<run_id>")
  │    → try_mark_dispatched（条件更新，独立短事务）
  │    → 失败：attempt_count + 1，指数退避，达上限进入 failed
  └─ ARQ Job execute_run(run_id)
       → RunExecutionService.execute
            → 条件认领 QUEUED → RUNNING（+ run_started Event）
            → 事务外执行占位执行体（切片 6 接入真实解析）
            → 条件推进 RUNNING → SUCCEEDED/FAILED（+ 对应 Event）
```

- 数据库提交与队列投递之间不假设原子性：Outbox 是持久化间隙，崩溃后下一轮派发循环补投；
- 外部队列调用发生在数据库事务外，每条 Outbox 记录的标记独立提交；
- Worker 从 PostgreSQL 读取事实，ARQ Job 只携带 `run_id`，ARQ Result 不作业务事实。

## 状态、数据模型和事务

- `QueueOutbox`：`outbox_id`、`run_id`（唯一）、`status`（`pending`/`dispatched`/`failed`）、`attempt_count`、`scheduled_at`、`dispatched_at`、`created_at`、`updated_at`。
- `run_id` 唯一约束保证一个 Run 最多一条投递记录。
- 投递失败退避：`min(2^(n-1), 60s)`；达到 `outbox_max_attempts`（默认 10）进入 `failed` 终态，等待人工介入。
- `try_mark_dispatched` 是 `WHERE status='pending'` 的条件更新，重复标记只有一个生效。

## 关键决定与替代方案

- **Outbox 而不是 API 直接投递**：API 直接 enqueue 会在“DB 已提交、队列未投递”的崩溃窗口丢任务；Outbox 把投递变成可重放的后台动作。
- **ARQ Job ID 去重**：`_job_id = "run:<run_id>"`，队列内同一 Run 同一时间只有一个待执行 Job，Outbox 补投天然去重。
- **Worker 端幂等执行**：`RunExecutionService` 只认领 `QUEUED` 的 Run（条件更新），重复 Job、已终态、已取消一律跳过。
- **`max_tries = 1`**：ARQ 不叠加自动重试，重试只有 Outbox 退避一层主导，避免多层重试相乘。
- **派发器在 Worker 进程内**：首版不单建 Dispatcher 部署单元；多实例并发派发时由 Job ID 去重和条件更新兜底。
- **API 侧队列连接惰性建立**：lifespan 注入 `ArqRunQueue`，连接池首次投递时才创建，API 启动不强依赖 Valkey 可用。

## 失败、重试、重复和取消行为

- 队列不可用：投递抛错 → `attempt_count + 1`、按退避推迟，记录保持 `pending`，恢复后补投（有集成测试）。
- 投递成功但标记前崩溃：记录保持 `pending`，下一轮重复投递，队列去重 + Worker 幂等保证 Effectively Once。
- 执行体抛错：Run 进入 `FAILED` 并写 `run_failed` Event，Event 只记录错误类型和截断消息，不记录堆栈。
- 执行期间并发取消：完成时条件更新失败，返回 SKIPPED，不产生第二个终态。
- Worker 崩溃导致 Run 停留在 `RUNNING` 的对账恢复（lease/heartbeat）属于切片 8。

## 安全和可观测性

- Job Payload 只有 `run_id`；Event Payload 不保存 PDF、全文或堆栈。
- 派发失败和执行失败通过 `logging` 记录 `run_id`/`outbox_id` 与异常信息。
- 配置项：`AGENT_REDIS_URL`、`AGENT_OUTBOX_POLL_INTERVAL_SECONDS`、`AGENT_OUTBOX_MAX_ATTEMPTS`、`AGENT_OUTBOX_DISPATCH_BATCH_SIZE`。

## 重要测试和运行结果

- `tests/domain/test_queue_outbox.py`：创建默认值、标记投递、失败退避、终态、退避上限。
- `tests/application/test_outbox_dispatch_service.py`：派发成功、未到期跳过、失败退避、上限进入 FAILED、崩溃后补投安全。
- `tests/application/test_run_execution_service.py`：QUEUED → SUCCEEDED、重复 Job 跳过、缺失/已取消跳过、执行失败进入 FAILED、并发只有一个完成。
- `tests/integration/test_outbox_repository.py`：PostgreSQL 唯一约束、外键、到期查询、`try_mark_dispatched` 条件更新。
- `tests/integration/test_queue_worker.py`：Valkey + ARQ + PostgreSQL 端到端——Outbox → ARQ → Worker → Run SUCCEEDED；队列故障后恢复补投；相同 Job ID 去重。

当前全部通过：`uv run pytest -q` 90 passed，`ruff check` 与 `pyright` 无告警。

## 代码入口

- 领域：`backend/src/literature_agent/domain/queue_outbox.py`
- 端口：`backend/src/literature_agent/application/ports/outbox_repository.py`、`run_queue.py`
- 服务：`backend/src/literature_agent/application/outbox_dispatch_service.py`、`run_execution_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/persistence/outbox_repository.py`、`infrastructure/queue/arq_run_queue.py`
- Worker 入口：`backend/src/literature_agent/worker.py`
- 迁移：`backend/migrations/versions/a4e0bc996e3b_create_queue_outbox_table.py`
- 部署：`deploy/compose/compose.yml`（valkey、worker）、`backend/Dockerfile`

## 已知限制

- 执行体仍是占位实现，不解析 PDF（切片 6）。
- 没有 Attempt/lease：`RUNNING` 状态的 Run 在 Worker 崩溃后不会自动对账（切片 8）。
- 单实例派发循环；多实例派发依赖 Job ID 去重和条件更新，未使用 SKIP LOCKED 认领。
- Outbox `failed` 终态暂无自动告警和人工重放入口。
- 没有 SSE 实时通知（切片 9）。

## 60 秒面试说明

"可靠投递模块解决数据库提交和队列投递之间的可靠性间隙。创建 Run 时在同一事务写一条 Outbox 记录，Worker 进程里的派发循环把到期记录投递到 ARQ——投递用 run_id 作为 Job ID，队列自动去重；标记投递用条件更新，崩溃后记录保持 pending 可以安全补投。Worker 执行时只认领 QUEUED 状态的 Run，重复 Job 直接跳过。这样任何一环崩溃或重复都不会丢任务或重复执行，业务上达到 Effectively Once，同时只有 Outbox 退避一层重试，ARQ 不叠加自动重试。"
