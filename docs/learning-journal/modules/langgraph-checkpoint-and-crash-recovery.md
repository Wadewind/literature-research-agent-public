# LangGraph Checkpoint 与崩溃恢复

## 模块解决的问题

固定 Review Workflow 需要在 Worker 进程退出后继续同一条图历史，同时不能把 LangGraph 状态误当成
业务 Run、权限、Event 或 Artifact。本模块接入真实 PostgreSQL Checkpointer，固定业务 Run 到 Thread
的映射，并补偿业务状态先提交、Attempt best-effort 关闭前崩溃留下的运维记录。

## 边界与执行流程

```text
ReviewWorkflowRuntime.start(initial_state)
  → thread_id = review.v1:review-run:{review_run_id}
  → 根图 checkpoint_ns = ''（LangGraph 保留给子图）
  → PostgreSQL checkpoint

Worker 崩溃
  → 新进程/新连接重建 Graph + Runtime
  → resume(review_run_id) → ainvoke(None, 同一 Thread 配置)
  → 从 pending checkpoint 续跑

人工输入已持久化
  → resume_human_input(review_run_id, request_id, human_input_id)
  → Command(resume={request_id, human_input_id})
  → review_outline 从头重执行并返回 Resume 值
  → 后继节点从业务库复核 Request/HumanInput/Outline 闭包

周期 RunReconcileService
  ├─ 原有：仍 RUNNING 且 lease 过期 → 收回 Run、失败重试
  └─ 新增：业务 Run 已离开旧执行边界 → 关闭残留 RUNNING Attempt
```

`ReviewGraphFactory` 保留可注入单切片的 crash-recovery 测试骨架，并在切片 7 增加真实
`propose_outline → review_outline interrupt → apply_outline_decision` 边界。feedback 路由回到
proposal 并再次 interrupt；切片 8–9 已继续接通 Section、Citation、Consistency、Artifact 与
Finalize，并把 Review Executor 注册到生产 `RunDispatcher`。

## 状态、数据与生命周期

- Graph State 只包含 Run/Project/Output/Source 等稳定 ID、小型版本和计数，不保存 PDF、Chunk 全文、
  Prompt、模型原始输出或最终文档；
- `thread_id` 固定为 `review.v1:review-run:{review_run_id}`，Workflow 版本同时进入 metadata；真实
  PostgreSQL 验证表明 LangGraph 1.2.10 根图会使用空 `checkpoint_ns`，该字段保留给子图内部命名，
  因此不能把 Workflow 版本错误地声称为根 namespace；
- `start()` 只接受首次完整 State；`resume()` 只接受业务 Run ID，并向 LangGraph 传 `None`，避免把
  恢复误当成同一 Thread 的新一轮输入；
- HITL 不复用 `resume(None)`：HumanInput 必须先在业务事务中持久化、解决当前 Request 并重新调度，
  Runtime 随后用 `Command(resume={request_id,human_input_id})` 恢复。Command 只传小型稳定 ID，
  不能替代 owner/Project/Run 授权和数据库闭包校验；
- Checkpointer 使用显式 `AsyncConnection` 生命周期、`autocommit=True`、`dict_row`，不泄漏连接；
- Runtime 不调用官方 `setup()`。Alembic migration 创建并版本化官方 3.1.1 的四张表、三个索引和
  migration 版本 0–9；
- 官方 Adapter 使用 raw psycopg 而非 ORM，因此四张表没有加入业务 `Base.metadata`；Alembic
  `include_object` 显式排除这些已由手写 revision 管理的表/索引，避免 autogenerate 静默生成 drop；
  schema 兼容性由固定依赖版本、migration 循环和真实 Checkpointer 测试共同保护；
- Serializer 禁止 pickle，并显式使用空的自定义模块 allowlist，只允许内建安全类型。

## 事务与幂等边界

Checkpoint 与业务副作用不是一个原子事务。节点遵循“业务结果先以稳定幂等键提交，再返回小型
Output ID”：若业务提交后、下一 checkpoint 前崩溃，节点会重放，但应用服务/唯一约束返回原结果，
不重复创建 Paper、Output 或 Artifact。Checkpoint 只决定从哪里继续，不能承担业务副作用去重。

## Attempt crash gap

原执行路径先提交 Run 的等待/终态，再 best-effort 关闭 Attempt，二者之间存在崩溃窗口。新增残留
对账有界扫描 RUNNING Attempt：

- Run 仍 `RUNNING` 或 `CANCEL_REQUESTED` 时保护最新 Attempt；
- 只对非最新 Attempt 或 Run 已进入其他状态的候选，在锁定 Run 后二次校验；
- 使用候选 Attempt 到下一 Attempt 之间的持久 Event 判断原因：依赖/输入等待 → `PAUSED`，失败重试
  → `FAILED`，取消 → `CANCELLED`，不根据 `run_type` 猜测；
- 当前等待或终态本身是无歧义事实时直接映射；证据不足时保持不动；
- `finish_if_running` 条件更新使重复或并发 Reconciler 只有一次效果。

原有“Run 仍 RUNNING 且最新 Attempt lease 过期”的收回/失败预算语义保持不变。若 Run 已进入
`CANCEL_REQUESTED`，新鲜心跳仍留给 Worker 协作收尾；只有最新 Attempt 的 lease 过期时，对账才在
同一持锁事务将 Attempt 与 Run 收敛为 `CANCELLED` 并写 `run_cancelled`，不进入失败重试，也不重置
Outbox。

## 失败、取消和安全

- psycopg checkpoint 读写错误归一化为临时 `CheckpointUnavailableError`；
- Workflow 版本不匹配或恢复结果缺失归为永久 `CheckpointDataError`；节点自己的 `ValueError`/
  `TypeError` 原样传播，避免把业务失败误报为 checkpoint 损坏；
- `CANCEL_REQUESTED` 最新 Attempt 不由残留对账提前关闭；心跳新鲜时继续由 Worker 协作完成，lease
  过期才由崩溃对账安全收敛；
- strict msgpack 降低被篡改 checkpoint 实例化任意 Python 类型的风险；业务权限仍须单独校验。

## 重要测试和运行结果

- Runtime：稳定 Thread/namespace、start/resume 分离、完成后重复恢复不重跑节点、读写错误分类；
- PostgreSQL：两个全新 connection/checkpointer/runtime 跨实例恢复 pending task，不同 Run 隔离，
  副作用后崩溃重放只保留一份幂等业务结果；
- HITL：MemorySaver 验证 approve/edit 与 feedback 再次 interrupt；真实 PostgreSQL Saver 验证首个
  Runtime interrupt 后关闭连接，新 Runtime/新连接通过同 Thread 的 Command Resume，proposal 和
  decision 各只执行一次；
- Attempt：正常恢复旧 Attempt→PAUSED、新 Attempt 不动；Review 失败重试旧 Attempt→FAILED；
  新鲜 `CANCEL_REQUESTED` Attempt 不动，过期后 Run/Attempt→CANCELLED 且不重试；并发 PostgreSQL
  Reconciler 单效果；
- Alembic：实际执行 `upgrade head → downgrade -1 → upgrade head`，`alembic check` 无漂移；
- 完整回归：非集成 `475 passed, 4 skipped`，PostgreSQL/Valkey integration `102 passed`；`ruff check`
  （`src`、`tests`、迁移环境与本切片 revision）、`pyright`、`git diff --check` 均通过。

## 代码入口

- 图与 Runtime：`backend/src/literature_agent/workflows/review_graph.py`
- Checkpointer：`backend/src/literature_agent/infrastructure/workflow/postgres_checkpoint.py`
- Attempt 对账：`backend/src/literature_agent/application/run_reconcile_service.py`
- 迁移：`backend/migrations/versions/b9d4e7f1a2c6_create_langgraph_checkpoint_tables.py`

## 已知限制

- 固定图与生产 Review Executor 已接通 Outline interrupt/feedback、章节、引用、一致性、Artifact 和
  Finalize；损坏 checkpoint 仍稳定失败，不会被当作首次 start 覆盖；
- Checkpoint 尚无按删除 Project/Run 的清理和保留策略；
- Attempt 分类依赖业务 Event 完整性；无法确定原因时安全保留记录，后续可增加告警；
- 每个显式 Context 使用一个 psycopg 连接；当前由 Worker Checkpoint 生命周期管理，尚未引入连接池。

## 60 秒面试说明

“Review Run 稳定映射到 `review.v1:review-run:{run_id}` Thread，版本前缀隔离 Workflow 历史。
Worker 重启时重建全新 Checkpointer 和 Runtime，调用 `ainvoke(None)` 从 pending checkpoint 恢复；
完整 State 只允许首次 start。Checkpoint 与业务事务不能原子提交，所以节点副作用仍由业务幂等键
保证，崩溃重放复用原结果。数据库表由 Alembic 管理，Serializer 禁用 pickle 和任意模块反序列化。
Reconciler 再用 Attempt 时间区间内的 Event 区分暂停和失败，关闭状态先提交后遗留的 Attempt。”
