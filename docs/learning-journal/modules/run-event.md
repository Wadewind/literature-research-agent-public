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
              └→ CANCEL_REQUESTED → CANCELLED
  QUEUED → CANCELLED
  ```
- `Run` 是不可变 dataclass，保存 `run_id`、`project_id`、`owner_id`、`run_type`、`status`、`input_payload`、`result_payload`、`event_sequence`、`created_at`、`updated_at`。
- `Event` 是不可变值对象，字段包含 `event_id`、`event_version`、`event_type`、`run_id`、`sequence`、`occurred_at`、`actor_type`、`correlation_id`、`payload`。
- `event_sequence` 表示下一个可用序号；创建 Run 后从 2 开始（`run_created` 占 sequence=1）。
- 数据库：`runs` 表、`events` 表，唯一约束 `(run_id, sequence)`。

## 关键决定与替代方案

- **SSE 以 PostgreSQL 为事实来源**（切片 9）：事件永远从数据库读取，Valkey Pub/Sub 通知只触发"去查库"，丢失通知由 1s 轮询兜底收敛；不追求跨进程 Exactly Once。
- **SSE id 直接用 sequence 字符串**：`Last-Event-ID` 携带已收到的最大 sequence，重连后重放其后全部历史，不重不漏；不引入全局 event UUID 游标。
- **通知注入点在应用服务**（切片 9）：写事件的服务在 commit 后调用 `EventNotifier.notify(run_id)`（publish 失败只记日志）；替代方案（Repository 层钩子）会把外部调用带进事务，违反边界。
- 状态机在领域对象中实现，而非散落在 Service 或数据库触发器，便于测试和保证不变量。
- 使用 `event_version` 为 Event 契约预留版本化空间。
- 用 `RunRepository.update_status` 做条件更新，捕获并发冲突并映射为 `RunConcurrentModificationError`。
- 显式引入 `Session` Port，让应用服务的事务代码同时适配 SQLAlchemy `AsyncSession` 和测试 Fake。

## 失败、重试、重复和取消行为

- 非法状态转换抛出 `InvalidRunTransitionError`，映射为 HTTP 409。
- 并发修改（行锁后发现状态与预期不符）抛出 `RunConcurrentModificationError`。
- `cancel_run`：QUEUED/RETRY_WAIT 直接 CANCELLED 并写 `run_cancelled`；RUNNING 先进入 CANCEL_REQUESTED 并写非终态 `run_cancel_requested`；Worker 协作完成取消后再写 `run_cancelled`。
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

切片 9 完成时的历史快照：`uv run pytest -q` 159 passed + 2 skipped（跳过项为显式启用的 Docling 真实解析组）。当前测试基线以 Phase 1 进度记录为准。

## 代码入口

- 领域：`backend/src/literature_agent/domain/run.py`、`backend/src/literature_agent/domain/event.py`
- 端口：`backend/src/literature_agent/application/ports/run_repository.py`、`event_repository.py`、`session.py`
- 服务：`backend/src/literature_agent/application/run_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/persistence/run_repository.py`、`event_repository.py`
- 路由：`backend/src/literature_agent/api/runs.py`

## 已知限制

- Queue Outbox、ARQ Worker 与 Attempt/lease 对账已接入（见 `queue-outbox.md`）。
- 没有 Step 抽象，复杂长任务的可观察粒度后续补充。
- 当前取消为协作式：RUNNING 状态写入 CANCEL_REQUESTED 后，需 Worker 在检查点响应。
- SSE 单连接每秒一次轮询查询，连接数大时需再评估（读扩散）。

## 60 秒面试说明

"Run/Event 模块是系统的可靠执行核心。领域层定义状态机和严格转换规则，Service 层通过行锁和条件更新处理并发，状态和事件在同一事务提交。Event 序列号保证历史顺序，REST 分页、SSE 重放和 Last-Event-ID 续传都以它为游标。SSE 的事件永远从 PostgreSQL 读，Valkey 通知只降延迟、丢了也有轮询兜底，所以断线或 Worker 重启后仍能从数据库恢复准确状态，不重不漏。"
