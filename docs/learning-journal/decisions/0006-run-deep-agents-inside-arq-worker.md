# ADR-0006：在 ARQ Worker 内运行 Deep Agents Runtime

- 状态：已接受
- 日期：2026-08-26
- 决策者：项目维护者

## 背景

Phase 5 切片 4/5 已证明 `DeepAgentsResearchAgentRuntime` 可以使用 PostgreSQL Checkpoint 跨连接重建
成功结果，Project Tool 也已有稳定 effect 与持久执行记录；但失败/取消终态仍依赖 Adapter 进程内状态，
新进程不会认领 orphan `RUNNING` checkpoint，生产 Worker 仍装配 Fake Runtime。

切片 6 必须明确 Deep Agents 的部署与恢复 owner。项目是个人简历项目，当前没有需要独立 Runtime 服务
解决的连接规模、资源隔离或扩缩容证据；未来不可信代码仍应在远端隔离 Sandbox 中执行，而不是在 Worker
宿主执行。

## 决策

Deep Agents 运行在现有 ARQ Worker 进程内，不新增独立 Runtime Deployment、内部 RPC 或第二条队列。
切片 6 只完成通过显式依赖注入构造真实 Deep Adapter 的跨进程恢复 Spike；生产 Worker 继续固定装配
`FakeResearchAgentRuntime`，当前不存在可由环境变量启用的真实 Deep Worker 模式。

`DeepAgentsResearchAgentRuntime` 必须注入 `BaseChatModel`。Provider/model factory、Secret 与费用策略，
以及 Worker `fake | deep_agents` 配置属于切片 7.0 “Real Deep Agent Runtime Enablement”的前置能力；本
切片既不选择真实 Provider，也不增加一个无法构造 model 的空开关或把测试 Fake 冒充为生产 Deep 模式。

新增 SDK-neutral 的 `agent_runtime_executions` 持久控制事实，保持以下映射：

```text
AgentTurnRun 1
  ├─ RunAttempt N              ARQ Worker 的业务投递与重试记录
  └─ RuntimeExecution 1        同一 Deep Agents Execution 的持久恢复控制
       └─ RuntimeLease         当前 Attempt 的短期执行权与 fencing token
```

- RuntimeExecution 不替代业务 Run、Attempt、Event 或 LangGraph Checkpoint；
- `turn_run_id` 唯一确定一个稳定 `runtime_execution_id` 和 `request_hash`；
- Runtime lease 绑定当前 RUNNING Attempt，认领时单调递增 fencing token；
- claim、renew、checkpoint 推进和终态写入必须同时匹配 owner、Attempt 与 fencing token；
- renew、checkpoint 与终态写入在同一短事务内锁定业务 Run、重新核对最新 RUNNING Attempt 和未过期
  lease/fence，再执行条件更新，不能只依赖事务前的活动性预检；
- `orphan` 是 `RUNNING` 且没有有效 lease 的派生条件，不增加会漂移的持久枚举；
- 终态不可重写；旧 owner 失权后不能发起新的模型/Tool 调用、推进 Execution 或提交业务结果；
- Runtime 调用和 Checkpoint I/O 不发生在业务数据库事务中，状态变化使用独立短事务；
- LangGraph 调用显式使用 `durability="sync"`；
- `last_checkpoint_id` 是平台已经观察并确认的恢复水位，不替代 Checkpointer 事实；无论水位为空，还是
  停在 C1 而 Checkpointer 已同步 C2，新 owner 都必须优先探测物理最新 Checkpoint，不能从旧水位重复
  已确认 Step；listing 与精确 checkpoint ID 均须匹配 Turn、Session、Execution、request hash 和
  runtime/graph revision，否则 fail-closed；
- 首次没有 checkpoint 时才追加本轮 HumanMessage；合法恢复使用
  `resume_turn(response=None)`，内部沿同一 Thread/Execution/Checkpoint 调用 `astream(None, ...)`；
- 恢复前重新加载原 ContextSnapshot/PolicySnapshot，不能使用新权限或新 Project 数据；
- temporary Runtime 错误保存安全错误并保留同一 Execution 的可恢复性；permanent、取消或预算耗尽才
  形成持久终态；
- 自动恢复只允许相同 runtime contract、graph revision、Deep Agents 与 LangGraph revision；不兼容时
  fail-closed 为 `runtime_version_incompatible`，Phase 5 不实现跨版本 checkpoint 迁移；
- Slice 7.0 因新增 checkpoint 私有预算 State 与 model middleware，将 graph revision 从
  `deep-agent-graph.v1` 升为 `deep-agent-graph.v2`；旧 v1 RuntimeExecution 或 Checkpoint 必须
  fail-closed，不能按新 State schema 自动恢复；
- 继续使用当前 PostgreSQL 数据库和 checkpoint schema，不增加独立数据库或 schema。

## Effectively Once 边界

切片 6 只承诺不会重复已经由同步 checkpoint 或成功 `ToolExecution` 持久确认的模型/Tool Step。
Provider 已收到请求但响应、ToolExecution 或 checkpoint 尚未持久化的在途窗口仍可能重试；没有 Provider
幂等键或补偿协议时不宣称 Exactly Once。后续 MCP、Browser、下载和 Sandbox 必须分别设计副作用保护。

## 后果

正面影响：复用既有 ARQ、Attempt、Outbox、Run Reconciler 与 PostgreSQL Checkpointer，以最小部署面
形成真实跨进程恢复证据；平台状态与 SDK 状态保持清晰分层。

代价：Worker 同时承担模型与图执行；需要维护 Runtime lease/fencing、同步 checkpoint 延迟和版本兼容
契约。未来若长时任务规模或隔离要求出现实际证据，可在不改变业务 Port 方法数的前提下，以新 ADR 迁移
到独立 Runtime Deployment。

切片 6 的“ARQ Worker 内运行”是部署拓扑决定与可执行恢复证据，不等于 Worker 已有真实 Provider 接线。
切片 7.0 完成前，生产路径仍只能运行 Fake Runtime。后续 7.0 已按本 ADR 的前置要求增加显式
`fake | deep_agents` 组合，默认 Fake 不变；Provider 与费用边界见 ADR-0007 和 Phase 5 Spec。

## 被否决的方案

- 只把 RunAttempt 当 Runtime lease：无法表达 Turn 级唯一 Execution、Checkpoint、Runtime 终态、版本
  与 fencing generation；
- 独立 Runtime Deployment：当前会新增服务认证、RPC、网络故障和双层调度，超出受限 Spike；
- 使用 LangGraph 默认 `async` durability：下一 Step 可在前一 checkpoint 完全持久化前开始，不满足本
  切片的恢复证据门槛；
- 自动跨版本恢复 checkpoint：快速变化的 SDK/Graph State 缺少兼容证据，必须 fail-closed。

## 与其他 ADR 的关系

- ADR-0001 继续决定 Deep Agents 选型和 `ResearchAgentRuntime` 隔离边界；
- ADR-0005 继续决定 Session/Turn/Thread/Execution 产品映射；
- 本 ADR 只决定 Runtime 部署、恢复所有权、durability 和版本兼容策略。
