# Run / Event 模块

## 解决的问题

为长时间运行的后台任务（文献导入、综述 Workflow、后续 Agent Run）提供统一的生命周期、事件历史和状态机。用户可以在页面刷新或断线后查询任务状态和可重放的事件流；系统可以据此实现重试、取消和故障恢复。

## 边界与执行流程

```text
HTTP Route (api/runs.py)
  → RunService (application/run_service.py)
    → create_run / start_run / complete_run / fail_run / cancel_run
      → RunRepository + EventRepository
        → PostgreSQL Adapter
          → runs / events 表
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

- 状态机在领域对象中实现，而非散落在 Service 或数据库触发器，便于测试和保证不变量。
- 使用 `event_version` 为 Event 契约预留版本化空间。
- 用 `RunRepository.update_status` 做条件更新，捕获并发冲突并映射为 `RunConcurrentModificationError`。
- 显式引入 `Session` Port，让应用服务的事务代码同时适配 SQLAlchemy `AsyncSession` 和测试 Fake。

## 失败、重试、重复和取消行为

- 非法状态转换抛出 `InvalidRunTransitionError`，映射为 HTTP 409。
- 并发修改（行锁后发现状态与预期不符）抛出 `RunConcurrentModificationError`。
- `cancel_run`：QUEUED/RETRY_WAIT 直接 CANCELLED；RUNNING 先进入 CANCEL_REQUESTED；CANCEL_REQUESTED 再次取消进入 CANCELLED。
- 本切片不触发真实 Worker，Run 创建后停留在 QUEUED；重复 API 调用或重复 Job 由后续幂等和 Outbox 机制处理。

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

当前全部通过：`uv run pytest -v` 62 passed。

## 代码入口

- 领域：`backend/src/literature_agent/domain/run.py`、`backend/src/literature_agent/domain/event.py`
- 端口：`backend/src/literature_agent/application/ports/run_repository.py`、`event_repository.py`、`session.py`
- 服务：`backend/src/literature_agent/application/run_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/persistence/run_repository.py`、`event_repository.py`
- 路由：`backend/src/literature_agent/api/runs.py`

## 已知限制

- 尚未接入 ARQ Worker、Queue Outbox 和 SSE。
- 没有 Attempt 和 Step 抽象，复杂长任务的可观察粒度后续补充。
- 当前取消为协作式：RUNNING 状态写入 CANCEL_REQUESTED 后，需 Worker 在检查点响应。

## 60 秒面试说明

"Run/Event 模块是系统的可靠执行核心。领域层定义状态机和严格转换规则，Service 层通过行锁和条件更新处理并发，状态和事件在同一事务提交。Event 序列号保证历史顺序，用户查询和后续 SSE 重放都以此为游标。这样断线或 Worker 重启后，仍能从 PostgreSQL 恢复准确状态。"
