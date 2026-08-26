# Agent Runtime Execution 恢复控制

## 模块解决的问题

Phase 5 切片 6 为 `AgentTurnRun` 增加 SDK-neutral 的持久 Runtime Execution 控制事实，使运行在 ARQ
Worker 内的 Deep Agents 可以在旧进程退出后由新 Attempt 受控接管，而不把 `RunAttempt`、LangGraph
Checkpoint 或 Adapter 内存状态误作同一件事。

## 边界和执行流程

```text
AgentTurnRun（业务生命周期，PostgreSQL）
  ├─ RunAttempt N（ARQ 投递/重试/Worker heartbeat）
  └─ RuntimeExecution 1（同一逻辑 Deep Agents Execution）
       ├─ current_attempt_id + lease_owner_id + lease_expires_at
       ├─ monotonic fencing_token
       ├─ last_checkpoint_id
       └─ runtime/graph/SDK revisions + 安全终态/错误

Attempt 1 claim fence=1
  → graph.astream(HumanMessage, durability="sync")
  → 每个已观察到的 checkpoint 由短事务推进 last_checkpoint_id
  → 进程退出、Attempt 1 失败、lease 过期
  → Attempt 2 条件 claim fence=2
  → 重新读取原 ContextSnapshot/PolicySnapshot
  → graph.astream(None, 同一 Thread/Execution/Checkpoint, durability="sync")
  → Runtime SUCCEEDED
  → 独立业务事务提交 Assistant Message/Event/Binding
```

Deep Agents 仍通过五方法 `ResearchAgentRuntime` Port 使用；没有增加 SDK Thread、Checkpoint、Command
或 Graph State 类型。`RuntimeExecutionControl` 是 Adapter 内部依赖，不是公开 API。

## 状态、数据模型和事务

`agent_runtime_executions` 以 `turn_run_id` 为主键，另对稳定 `runtime_execution_id` 唯一约束。记录只保存
稳定 ID、SHA-256 请求哈希、版本、状态、fence、lease、最后确认 checkpoint、安全错误与时间，不保存
Prompt、消息/论文/网页正文、Secret、SDK 对象或大型 Tool 输出。

- `RuntimeExecution` 与 `AgentTurnRun` 为 1:1；`RunAttempt` 与它为 N:1；
- lease 必须绑定当前最新的 RUNNING Attempt，且业务 Run 必须为 RUNNING；
- orphan 是 `RUNNING` 且 lease 缺失或过期的派生判断，不持久化第二套易漂移状态；
- 重复 owner/Attempt 在有效期内 renew 不增加 fence；新 owner 接管 orphan 时 fence 单调加一；
- claim 使用 Run/Execution 行锁与唯一约束，后续保存再以旧 state、owner、Attempt、fence 做 CAS；
- renew、checkpoint、成功、临时/永久错误都先复核 Run、Attempt、lease 与 fence；过期 owner 不能写终态；
- 取消必须先从 PostgreSQL 读到业务 `CANCEL_REQUESTED/CANCELLED`，不能仅凭 Runtime 调用改写；
- 终态不可重写。temporary 错误只保存有界安全 code/message、释放 lease 并保留 RUNNING；
- 所有控制事务都是短事务；模型、Tool 与 Checkpoint I/O 均在事务外；
- `last_checkpoint_id` 是平台已观察到的恢复水位，而不是第二份 Checkpoint 事实。水位为空或停在旧 C1、
  物理 C2 已同步的窗口中，新 owner 都优先探测物理最新 Checkpoint；listing 与精确 ID 均校验 Turn、
  Session、Execution、request hash 和 runtime/graph revision；
- Runtime 成功和业务结果提交是两个阶段。响应丢失后，新的业务 Attempt 从持久 Runtime 成功结果 collect，
  仍由业务唯一约束和条件更新保证 Assistant Message/Event 只提交一次。

## 恢复与版本兼容

锁文件与本地安装均确认 `deepagents==0.7.8`、`langgraph==1.2.11`。Runtime 记录和 checkpoint metadata
同时固定 runtime contract 与 graph revision；恢复要求请求哈希、Session、Execution、runtime/graph revision
以及两个 SDK 版本完全一致，否则以安全 permanent `runtime_version_incompatible` fail-closed。本切片不做
checkpoint 跨版本迁移。

恢复分两种崩溃点：

- 已有 checkpoint：新 owner 不把控制水位当作物理最新事实；选择并校验最新 Checkpoint 后调用既有
  `resume_turn(response=None)`，内部只执行 `astream(None, ...)`，不追加 HumanMessage，也不重放旧水位
  与物理最新之间已确认的 Step；
- DB 已 claim、尚无首个 checkpoint：消息还未形成持久模型上下文，新 owner才重新执行首次
  `astream({HumanMessage...})`。测试验证最终 Graph State 中该 `user_message_id` 仍只有一次。

`FAILED`、`CANCELLED`、`SUCCEEDED` 由新 Adapter 直接从 RuntimeExecution 对账；成功还必须能读取记录指定
的 checkpoint，不能只凭数据库枚举伪造结果。

## Effectively Once 与取消边界

Adapter 在模型和 Tool middleware 的实际调用边界复核 permit；旧 owner 失权后不能发起下一次调用。
LangGraph 显式使用 `durability="sync"`，因此下一 Step 只在上一 checkpoint 同步持久后开始。平台只承诺
不重复同步 checkpoint 或成功 `ToolExecution` 已确认的调用。

真实 OS 进程测试选择的崩溃点是：Tool Step 已完成并形成同步 checkpoint，下一次模型调用已发起但尚未
返回。杀死进程后，第二进程不重放已确认的首个模型与 Tool；未确认的在途模型调用会重试。这正是边界，
没有 Provider 幂等键/调用账本时不宣称 Exactly Once。

Project Tool 在 `ToolExecution=RUNNING` 且进程消失的未确认窗口不会被盲目重放：当前返回 temporary
`project_context_effect_in_progress`，避免猜测外部效果是否完成。这也意味着该 effect 不能自动收敛，需在
接入具体外部 MCP/Browser/Sandbox 前按工具副作用设计查询、幂等键或补偿协议。

业务进入 `CANCEL_REQUESTED` 后，Runtime 持久化 `CANCELLED`，新进程只对账、不 resume；Adapter 的在途
consumer 被取消，middleware 阻止失权后的新模型/Tool。已经发出的远端调用不保证可立即中止。

## 重要测试和运行结果

2026-08-26 开发过程中的真实证据：

- Domain/Application 首轮因模块不存在得到 2 个 collection error；最小实现后 6 passed；
- `durability="sync"` 与 `resume_turn(None)` 首轮 2 failed，修复后 2 passed；
- 持久 fence、跨 Adapter 失败/取消终态、版本不兼容定向测试 3 passed；
- 取消授权补强先 1 failed，修复后 Application 3 passed；过期 owner 错误终态补强先 2 failed，修复后
  RuntimeExecution Domain/Application 合计 9 passed；稳定 Session/Execution 身份补强首轮 3 failed，
  修复后对应 Domain/Application 套件 11 passed；活动性预检与控制写入之间 Attempt 失效的竞争测试首轮
  1 failed，写事务内重新核对 Run/Attempt/lease/fence 后 Domain/Application 套件 12 passed；
- 同步 Checkpoint 已写入但控制水位未推进的恢复测试首轮 1 failed，修复后 1 passed，代理确认恢复调用的
  图输入为 `None`；
- 非空旧水位 C1/物理最新 C2 测试首轮 1 failed，修复后确认从 C2 恢复、C2 前模型调用不重放；精确
  checkpoint ID 的 Session metadata 篡改测试首轮 1 failed，修复后以 permanent
  `runtime_checkpoint_identity_mismatch` 拒绝；两项定向最终为 2 passed in 0.80s；
- PostgreSQL 两个并发恢复者测试在修正测试方法名后 1 passed；最多一个 owner 获得 fence=2；
- 真实 OS 进程测试在修正测试场景 FK 构造后 1 passed in 8.14s；第一个进程被真实 terminate，第二个
  spawn 进程完成恢复，已确认模型/Tool 各一次，未确认模型调用明确重试一次；
- Deep Adapter 完全离线最终回归 30 passed in 1.26s（使用既有已批准非沙箱执行方式；受控命令沙箱内的
  selector 假性等待仍不是产品 Sandbox 结论）。
- 最终相关 Domain/Application/Fake/Deep Adapter 回归 60 passed in 54.56s；Runtime/真实进程/Checkpoint/
  Agent PostgreSQL 回归 7 passed in 28.46s；Project Context 扩大回归 8 passed in 27.25s；
- 主智能体独立复验 Domain/Application/Deep Adapter 为 42 passed in 1.27s，Runtime 控制与
  真实跨进程恢复为 2 passed in 11.70s，迁移往返与 ORM 契约为 3 passed in 5.02s；
- 水位补强后主智能体完整非集成后端回归为 800 passed, 4 skipped in 76.94s；
- 本次水位补强后真实跨进程恢复定向复跑 1 passed in 8.41s；
- `ruff check src tests` 与 `git diff --check` 通过；`pyright src` 为 0 errors；Alembic 单 head 为
  `a4c8e1f2b7d9`；
- 一次使用 `./.venv/bin/pytest` 直接启动迁移测试时，子进程 PATH 找不到 `alembic`；改用项目标准
  `uv run pytest` 后通过。一次受限沙箱内 `uv run alembic heads` 因 uv 用户 cache 只读失败，改用同一
  虚拟环境的 `./.venv/bin/alembic heads` 后成功。这两项都是命令环境问题，不是迁移断言失败。
- 主智能体首次在受限沙箱内复跑 Adapter 套件时完成 12 项后无进展，已主动中止；使用既有
  已批准的非沙箱测试方式独立复跑通过。这仍不是对产品 Sandbox 或真实 Provider 的验证。

## 代码入口

- Domain：`backend/src/literature_agent/domain/runtime_execution.py`
- Application：`backend/src/literature_agent/application/runtime_execution_control.py`
- Ports：`backend/src/literature_agent/application/ports/runtime_execution_control.py`、
  `runtime_execution_repository.py`
- PostgreSQL：`backend/src/literature_agent/infrastructure/persistence/runtime_execution_repository.py`
- Deep Adapter：`backend/src/literature_agent/infrastructure/agent/deep_agents_research_agent_runtime.py`
- 迁移：`backend/migrations/versions/a4c8e1f2b7d9_add_agent_runtime_executions.py`
- 真实进程测试：`backend/tests/integration/test_agent_runtime_process_recovery.py`

## 已知限制

- 切片 6 当时只验证显式 DI 构造的真实 Deep Adapter；切片 7.0 已新增 Worker `fake | deep_agents`
  显式配置、固定 Provider factory 与 Secret/主模型费用边界，默认仍为 Fake；
- 真实模式复用本模块的 RuntimeExecution control 与持久 Checkpointer，但 Worker 当前只持有单
  `AsyncConnection` + singleton Saver。Saver 实例锁保证协程正确性但串行 checkpoint I/O，且仍有
  单连接故障面；pool + per-execution Saver/graph factory 留到 7.1；
- Slice 7.0 新增 checkpoint 私有预算 State 后将 graph revision 升为 `deep-agent-graph.v2`；旧 v1
  RuntimeExecution 和 Checkpoint 均按本模块兼容契约返回 `runtime_version_incompatible`，不自动迁移；
- 本切片不接真实 Provider、MCP、Browser、Sandbox 或 Skill；
- 未确认的 Provider/Tool 外部调用可能重试；Project Tool orphan RUNNING 不自动猜测并重放；
- 当前版本只支持完全相同 revision 自动恢复，没有 checkpoint migration 或运维审批流程；
- Runtime lease 是进程接管控制，不替代业务 Attempt lease、Provider 取消协议或正式 Artifact 提交；
- 没有证明高并发吞吐、checkpoint sync 性能、跨主机网络分区或公网生产 SLA。

## 60 秒面试说明

“我让 Deep Agents 留在 ARQ Worker 内，但没有把 Worker Attempt 当作 Agent Execution。我为每个业务 Turn
增加一条 SDK-neutral RuntimeExecution，绑定当前 Attempt 的短 lease 和单调 fencing token。模型、Tool
和 checkpoint 前都会复核 lease，LangGraph 用 sync durability。旧进程被真实杀死后，新 Attempt 只认领
过期 lease，重新读取原 Context/Policy，并以 `astream(None)` 沿同一 checkpoint 继续。测试证明同步确认
的模型/Tool 不重复，而未确认的在途模型会重试，所以我明确称它为 Effectively Once 边界而不是 Exactly
Once。Runtime 成功仍需另一个短事务提交业务 Message/Event，SDK 状态从不替代 PostgreSQL 业务事实。”
