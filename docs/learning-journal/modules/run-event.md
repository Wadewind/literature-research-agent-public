# Run / Event 模块

## 解决的问题

为长时间运行的后台任务（文献导入、综述 Workflow、后续 Agent Run）提供统一的生命周期、事件历史和状态机。用户可以在页面刷新或断线后查询任务状态和可重放的事件流；系统可以据此实现重试、取消和故障恢复。

## 边界与执行流程

```text
HTTP Route (api/runs.py)
  → RunService (application/run_service.py)
    → create_run / cancel_run / list_events(after_sequence, limit)
      → RunRepository + EventRepository
        → PostgreSQL Adapter
          → runs / events 表

SSE (GET /runs/{run_id}/events/stream)
  → 每轮：独立短事务读 Run 状态 + list_after(last_sequence)
  → Valkey Pub/Sub 通知（run-events:{run_id}，可丢失）只用于降延迟
  → 1s 轮询兜底；终态且事件全部发送后关闭流
```

- `Run` 表示用户可见的任务，`Event` 表示任务历史中的事实。
- `RunService` 控制事务：状态转换、Event 写入在同一短事务提交。
- 状态转换由 `Run.transition_to` 在领域层断言合法性，非法转换抛出 `InvalidRunTransitionError`。
- 并发修改通过 `get_by_id_for_update` 行锁 + 条件更新 `update_status` 检测。

## 状态、数据模型和事务

- 状态机：
  ```text
  QUEUED → RUNNING → SUCCEEDED
              ├→ FAILED
              ├→ RETRY_WAIT → QUEUED
              ├→ WAITING_INPUT → QUEUED
              ├→ WAITING_DEPENDENCY → QUEUED
              └→ CANCEL_REQUESTED → CANCELLED
  QUEUED → CANCELLED
  RETRY_WAIT / WAITING_INPUT / WAITING_DEPENDENCY → CANCELLED
  ```
- `Run` 是不可变 dataclass，保存 `run_id`、`project_id`、`owner_id`、`run_type`、`status`、`input_payload`、`result_payload`、`event_sequence`、`created_at`、`updated_at`。
- `run_type` 取值受 `RunType` 枚举（`ingestion`/`indexing`/`rag_answer`）约束（Phase 2 切片 4）：DB 列保持自由字符串以兼容历史数据，`create_run` 创建时校验枚举取值，非法值直接 `ValueError`。
- `Event` 是不可变值对象，字段包含 `event_id`、`event_version`、`event_type`、`run_id`、`sequence`、`occurred_at`、`actor_type`、`correlation_id`、`payload`。
- `event_sequence` 表示下一个可用序号；创建 Run 后从 2 开始（`run_created` 占 sequence=1）。
- 数据库：`runs` 表、`events` 表，唯一约束 `(run_id, sequence)`。
- `WAITING_INPUT` 和 `WAITING_DEPENDENCY` 是活跃业务状态，但不占用 Worker；恢复时先回到
  `QUEUED`。对应 Worker Attempt 以 `PAUSED` 结束，恢复后的 Job 创建新 Attempt。

## 关键决定与替代方案

- **SSE 以 PostgreSQL 为事实来源**（切片 9）：事件永远从数据库读取，Valkey Pub/Sub 通知只触发"去查库"，丢失通知由 1s 轮询兜底收敛；不追求跨进程 Exactly Once。
- **SSE id 直接用 sequence 字符串**：`Last-Event-ID` 携带已收到的最大 sequence，重连后重放其后全部历史，不重不漏；不引入全局 event UUID 游标。
- **通知注入点在应用服务**（切片 9）：写事件的服务在 commit 后调用 `EventNotifier.notify(run_id)`（publish 失败只记日志）；替代方案（Repository 层钩子）会把外部调用带进事务，违反边界。
- 状态机在领域对象中实现，而非散落在 Service 或数据库触发器，便于测试和保证不变量。
- 使用 `event_version` 为 Event 契约预留版本化空间。
- 用 `RunRepository.update_status` 做条件更新，捕获并发冲突并映射为 `RunConcurrentModificationError`。
- 显式引入 `Session` Port，让应用服务的事务代码同时适配 SQLAlchemy `AsyncSession` 和测试 Fake。
- **正常恢复使用受限事务服务**（Phase 3 切片 1）：`WaitingRunResumeService` 校验 owner、Project
  和等待原因；`resume_in_session()` 允许后续服务在同一 session 保存 HumanInput/Dependency，
  再把等待 Run 重新排队、写原因 Event 与重置 Outbox，由最外层统一提交。Checkpoint 或队列状态
  都不能代替业务记录与 Event。

## 失败、重试、重复和取消行为

- 非法状态转换抛出 `InvalidRunTransitionError`，映射为 HTTP 409。
- 并发修改（行锁后发现状态与预期不符）抛出 `RunConcurrentModificationError`。
- `cancel_run`：QUEUED/RETRY_WAIT/WAITING_INPUT/WAITING_DEPENDENCY 直接 CANCELLED 并写
  `run_cancelled`；RUNNING 先进入 CANCEL_REQUESTED 并写非终态 `run_cancel_requested`；
  Worker 协作完成取消后再写 `run_cancelled`。
- 等待恢复若原因与状态不匹配或已被其他调用恢复，条件状态拒绝第二次效果；Outbox 不存在或
  不是 `DISPATCHED` 时抛 `RunSchedulingError` 并回滚 Run/Event。
- 当前 Run 已由 Queue Outbox + ARQ Worker 驱动；重复投递和崩溃恢复见 `queue-outbox.md`。
- SSE 连接不持有数据库事务；每轮轮询是独立短事务。
- SSE 收束判断先读 Run 状态再读事件：终态与终态事件同事务提交，该顺序保证不重不漏。

## 安全和可观测性

- 查询始终校验 `owner_id`。
- Event Payload 只保存小型结构化数据，不保存 PDF、全文、Prompt 或堆栈。
- `correlation_id` 用于把同一业务操作的前端请求、Service 调用和 Event 串联。

## 重要测试和运行结果

- `tests/domain/test_run.py`：状态机合法/非法转换、取消。
- `tests/application/test_run_service.py`：创建/开始/完成/取消/事件写入。
- `tests/api/test_runs.py`：HTTP 路由映射。
- `tests/integration/test_run_repository.py`：PostgreSQL 查询与条件更新。
- `tests/integration/test_run_concurrency.py`：并发 start_run 仅一个成功。
- `tests/api/test_runs.py`：事件 `after_sequence`/`limit` 分页与参数校验。
- `tests/api/test_run_events_stream.py`：SSE 历史重放、Last-Event-ID 续传、终态关闭、通知丢失时轮询收敛、404/400。
- `tests/application/test_event_notification.py`：commit 后通知、publish 失败不影响业务。
- `tests/integration/test_event_notifier.py`：Valkey Pub/Sub 发布订阅回环（channel 按 run_id 隔离）。
- `tests/application/test_waiting_run_resume_service.py`：受限原因、所有权、重复恢复和 Outbox 前置条件。
- `tests/integration/test_waiting_run_resume_transaction.py`：Run/Event/Outbox 原子提交及异常回滚。

切片 9 完成时的历史快照：`uv run pytest -q` 159 passed + 2 skipped（跳过项为显式启用的 Docling 真实解析组）。当前测试基线以 Phase 1 进度记录为准。

Phase 3 切片 1 验证：Backend 非集成 `387 passed, 4 skipped`，完整 integration
`86 passed`；`ruff check src tests` 与 `pyright` 通过。Web 状态/Event 契约包含在
`65 passed` 的 Vitest 全量结果中，生产构建通过。

## 代码入口

- 领域：`backend/src/literature_agent/domain/run.py`、`backend/src/literature_agent/domain/event.py`
- 端口：`backend/src/literature_agent/application/ports/run_repository.py`、`event_repository.py`、`session.py`
- 服务：`backend/src/literature_agent/application/run_service.py`
- 正常恢复：`backend/src/literature_agent/application/waiting_run_resume_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/persistence/run_repository.py`、`event_repository.py`
- 路由：`backend/src/literature_agent/api/runs.py`

## 已知限制

- Queue Outbox、ARQ Worker 与 Attempt/lease 对账已接入（见 `queue-outbox.md`）。
- 没有 Step 抽象，复杂长任务的可观察粒度后续补充。
- 两种等待状态和恢复事务已可复用，但 Review Run、依赖 Reconciler 和 Human Input 调用方
  尚未接线，分别属于 Phase 3 后续切片。
- Run 已提交等待/终态后、Attempt 关闭前仍有崩溃间隙；现有 Reconciler 只关联仍为 RUNNING 的
  Run，无法清理这种残留 Attempt，需在 Phase 3 crash recovery 切片解决。
- 当前取消为协作式：RUNNING 状态写入 CANCEL_REQUESTED 后，需 Worker 在检查点响应。
- SSE 单连接每秒一次轮询查询，连接数大时需再评估（读扩散）。

## 60 秒面试说明

"Run/Event 模块是系统的可靠执行核心。领域层定义包括等待状态在内的严格状态机，Service 层
通过行锁和条件更新处理并发；正常恢复把 Run 重新排队、原因 Event 和 Outbox 重置放在同一
事务，避免出现已排队但无法投递的半状态。Event 序列号同时是 REST 分页和 SSE 断线重放游标；
SSE 始终从 PostgreSQL 读事实，Valkey 通知只降延迟，所以进程重启后仍能恢复准确业务时间线。"
