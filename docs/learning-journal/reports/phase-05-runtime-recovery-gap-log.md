# Phase 5 Runtime 部署与崩溃恢复缺口台账

## 状态与用途

状态：已闭合。记录日期：2026-08-25；决策与闭合日期：2026-08-26。

本报告记录 Phase 5 切片 4 的真实 Deep Agents Adapter Spike 暴露出的恢复证据缺口，以及切片 6 的闭合
证据。部署与恢复所有权已由 ADR-0006 固化，具体实现见 Agent Runtime Execution 恢复控制模块笔记。

该门槛已经通过，但不会自动授权进入后续切片；MCP、Browser、Sandbox、Skill 和 UI 仍按 Phase 5 Spec
逐项受限验证。

## 切片 4/5 暴露缺口时的已确认事实

- `DeepAgentsResearchAgentRuntime` 使用 `deepagents==0.7.8`、StateBackend 与 PostgreSQL Checkpointer；
- 成功 Execution 已通过关闭旧连接、创建同进程新 Adapter 的测试，按 checkpoint metadata 恢复结果，
  且没有再次调用 Fake Model 或确定性 Tool；
- 上述测试消除了对旧 Adapter 对象内存状态的依赖，但没有启动第二个 OS 进程；
- `FAILED`/`CANCELLED` Runtime 终态仍依赖在途 Adapter 的协作状态，没有独立持久 Runtime registry；
- 新 Adapter 遇到 orphan `RUNNING` checkpoint 不会自动认领或 resume；
- 生产 Worker 仍装配 Fake Runtime，因此 Deep Agents 位于 ARQ Worker 内还是独立 Runtime Deployment
  尚未决定。

## 三项耦合缺口

### P5-RUNTIME-001：缺少真实跨进程恢复证据

当前“新连接 + 新 Adapter”只模拟对象重建，不能证明旧执行进程退出后，新进程能以稳定
`turn_run_id` 找到同一 Thread/Execution/Checkpoint、继续执行并收集同一结果。

### P5-RUNTIME-002：Runtime 终态与 orphan RUNNING 不可持久对账

成功终态可以从 checkpoint 重建；失败、取消及执行中所有权没有等价的持久事实。旧进程崩溃后，平台
无法区分仍有合法 owner 的执行、已经失去 owner 的 orphan `RUNNING`、以及已经失败或取消但未完成业务
提交的执行。

### P5-RUNTIME-003：部署拓扑与恢复 owner 未决定

没有决定 Deep Agents 由 ARQ Worker Attempt 直接拥有，还是由独立 Runtime Deployment 拥有，也就无法
固定谁续租 Execution lease、谁认领 orphan、谁传播取消、何时允许业务 Attempt 重试。这个决定会影响
部署与故障边界，不能由 Adapter 私自选择。

## 闭合结果

- P5-RUNTIME-001：真实 spawn 两个 OS 进程；第一个在 Tool Step 同步 checkpoint 后被 terminate，第二个
  以新 Attempt/fence 沿同一 Execution/Checkpoint 完成；
- P5-RUNTIME-002：`agent_runtime_executions` 持久化 RUNNING/SUCCEEDED/FAILED/CANCELLED、最后
  checkpoint、版本与安全错误；orphan 由 lease 派生，两个并发恢复者最多一个认领；
- P5-RUNTIME-003：ADR-0006 决定 ARQ Worker 内运行，Runtime lease 绑定当前 Attempt 但不替代 Attempt；
- 取消要求业务 Run 已为 CANCEL_REQUESTED/CANCELLED；过期 owner 不能调用新模型/Tool、推进 checkpoint
  或写 Runtime 错误/成功终态；
- `durability="sync"` 只证明已确认 Step 不重放；测试明确观察到未确认的在途模型调用被重试；
- LangGraph Checkpoint 与平台 `last_checkpoint_id` 是两个独立提交；故障窗口测试覆盖空水位及非空旧水位
  C1/物理最新 C2。新 owner 优先探测并校验物理最新 C2，以 `astream(None)` 恢复，不重新传入
  HumanMessage，也不重放 C2 前已确认模型调用；错误 Session 等稳定身份安全拒绝；
- runtime/graph/Deep Agents/LangGraph revision 不一致时返回 permanent
  `runtime_version_incompatible`，不自动迁移 checkpoint。
- 本门槛只验证显式 DI 构造的真实 Deep Adapter；生产 Worker 仍固定 Fake，当前没有环境变量可启用的
  Deep Worker 模式。Provider/`BaseChatModel` factory、Secret/费用与 Worker runtime 配置移入切片 7.0。
  这是切片 6 验收时的事实；切片 7.0 随后已完成显式 Deep Worker enablement，默认仍为 Fake。

## 切片 6 的决策选项

### 选项 A：ARQ Worker 内运行

优点是符合个人简历项目的最小部署面，复用现有 Worker、Attempt lease、Outbox 和本地开发拓扑。代价是
必须清楚区分业务 Attempt lease 与 Runtime Execution lease，并证明新 Worker 进程可接管同一
checkpoint，而不是创建第二次逻辑执行。

### 选项 B：独立 Runtime Deployment

优点是长时间 Runtime 的 owner 和生命周期更集中，Worker 只负责提交/对账。代价是新增服务、部署配置、
认证、网络故障与双层 lease，明显扩大 Phase 5 的实现和运维范围。

项目维护者已接受选项 A，详见 ADR-0006。Deep Agents 在现有 ARQ Worker 内运行；平台新增独立于
RunAttempt 的 SDK-neutral RuntimeExecution 控制事实，以 Attempt 绑定 lease 和单调 fencing token 管理
同一逻辑 Execution 的恢复权。LangGraph 显式使用同步 durability，恢复只允许相同 Runtime/Graph 版本。

切片 6 只承诺不重复已经由同步 checkpoint 或成功 ToolExecution 持久确认的调用；在途外部调用仍不
宣称 Exactly Once。

## 切片 6 验收门槛

- 明确并记录部署拓扑、Runtime Execution lease/recovery owner、取消 owner 与业务 Attempt 的关系；
- 使用平台自有、SDK-neutral 的持久事实表达可对账状态，不把 SDK 类型放入 Domain、API 或业务 Event；
- 新进程能识别 orphan `RUNNING`，以条件认领/lease 防止两个 owner 同时恢复；
- 恢复沿用同一 Session Binding、Runtime Execution 与真实 Checkpoint，不重新追加用户消息；
- `FAILED`/`CANCELLED` 可在旧进程退出后由新进程安全对账，且不会提交 Assistant Message 或候选 Artifact；
- 至少一个测试实际终止或退出执行进程，并由第二个 OS 进程恢复；测试记录真实命令和结果；
- 恢复、重复 Job 与取消竞争不会重复已持久确认的模型/Tool 调用；Event 仍只保存筛选后的安全事实；
- 所有普通测试保持离线、确定性、零费用，不访问真实 Provider、网站、MCP 或付费 Sandbox。

## 明确不随本门槛解决的窗口

Tool 已经产生外部副作用、但对应 checkpoint 或平台调用记录尚未提交时，单靠 LangGraph checkpoint 无法
证明 Effectively Once。切片 5 的正式 Project Tool 必须先引入稳定 call/effect ID、唯一约束或持久调用
记录；后续 MCP、Browser、下载和 Sandbox 还需各自设计幂等或补偿策略。本门槛只证明恢复不会重复已经
持久确认的调用，不宣称任意外部副作用 Exactly Once。

## 关联文档

- [Phase 5 Spec](../phases/phase-05-deep-agents-integration.md)
- [Deep Agents Runtime Adapter](../modules/deep-agents-runtime-adapter.md)
- [Agent Runtime 业务契约](../modules/agent-runtime-contract.md)
- [ADR-0001：选择 Deep Agents Runtime](../decisions/0001-select-deep-agents-runtime.md)
- [ADR-0005：交互式 Research Agent 会话模型](../decisions/0005-interactive-research-agent-session-model.md)
- [ADR-0006：在 ARQ Worker 内运行 Deep Agents Runtime](../decisions/0006-run-deep-agents-inside-arq-worker.md)
- [Agent Runtime Execution 恢复控制](../modules/agent-runtime-execution-recovery.md)
