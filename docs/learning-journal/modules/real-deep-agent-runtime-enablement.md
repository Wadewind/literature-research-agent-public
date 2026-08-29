# Real Deep Agent Runtime Enablement

## 模块解决的问题

Phase 5 切片 7.0 把此前只能由测试显式依赖注入构造的 `DeepAgentsResearchAgentRuntime` 接入 ARQ Worker，
同时保证默认开发、测试和演示仍使用离线 `FakeResearchAgentRuntime`。模块只解决真实 Provider 与既有恢复
基础设施的生产组合，不接 OpenSandbox、Browser、MCP、Skill 或 UI。

> 后续状态：Slice 7.1 已用固定 Capability Profile、OpenSandbox/WorkspaceSnapshot 和
> `AsyncConnectionPool` + per-operation Saver/graph 替代本模块记录的单连接/无 Sandbox 限制；本页其余内容
> 保留为 7.0 当时的实现证据。详见
> [`agent-sandbox-workspace.md`](agent-sandbox-workspace.md)。

## 边界和执行流程

```text
Settings.from_env
  ├─ fake（默认）→ FakeResearchAgentRuntime；不读取 Agent Key、不建模型/Checkpointer
  └─ deep_agents → ChatDeepSeek factory
       → PostgresCheckpointStore.open（Worker 生命周期）
       → ProjectResearchContextService + RuntimeExecutionControlService
       → DeepAgentsResearchAgentRuntime
       → AgentTurnExecutor（只依赖 ResearchAgentRuntime Port）
```

Agent Provider 配置与 RAG/Review Chat 配置分离。真实模式固定
`langchain-deepseek==1.1.0` 与 `deepseek-v4-flash`；thinking 默认 `disabled`，只允许在
`AGENT_DEBUG=true` 的开发诊断中切换为 `enabled` 和受限 effort。Provider/PolicySnapshot 的单次输出上限
于 2026-08-30 同步调整为 4096，并继续设置 timeout 和 retry。
Domain、公开 API、业务数据库和 `ResearchAgentRuntime` Port 均没有新增 Deep Agents 或 ChatDeepSeek 类型。

## 状态、数据与事务

- Worker 为真实 Runtime 持有一个 `ChatDeepSeek`、一个现有 `PostgresCheckpointStore` connection 和一个
  singleton `AsyncPostgresSaver`，shutdown 时显式关闭；
- Project 权限、ContextSnapshot、PolicySnapshot、RuntimeExecution lease/fence、Run、Event、Evidence
  与 Artifact 仍由 PostgreSQL 业务事实控制；SDK Checkpoint 只保存模型工作状态；
- Worker 在短事务之外调用 Runtime。Provider 与 checkpoint I/O 不进入业务数据库事务；Runtime 成功仍
  需要 `AgentTurnExecutor` 的独立短事务提交 Assistant Message、引用、候选 Artifact 和 Event；
- Secret 只从专用 Agent 配置进入 factory，Settings 字段 `repr=False`，错误只包含稳定配置名，不包含 Key。
  API 与 Worker 可解析同一非敏感 runtime 配置，但缺 Key 校验发生在 Worker composition；本地启动脚本在
  Worker fork 后移除专用 Key，API、迁移和基础设施进程不持有 Agent Provider Secret。

## 主模型预算

`PolicySnapshot.max_model_calls` 在 7.0 被精确定义为当前 Turn 的**主 Agent Loop 模型调用预算**。
自定义 middleware 在每个主模型 node 前将 `turn_run_id → 已预留次数` 的私有状态更新写入 LangGraph
checkpoint；额度耗尽时在 Provider 调用前返回 permanent `runtime_model_call_limit_exceeded`。采用既有
`durability="sync"`，Tool node 失败后的恢复沿 checkpoint 保留额度，不重新获得逻辑调用次数。

Checkpoint 不保存按 Turn 累积的映射，只保存当前 budget `turn_run_id` 与一个预留计数。新 Turn 第一次
调用覆盖旧值；单活动 Turn 和终态后不恢复旧 Turn 的业务不变量使该状态保持常数空间。由于 State schema
和 model middleware 改变，graph revision 从 `deep-agent-graph.v1` 升为 `deep-agent-graph.v2`，旧 v1
RuntimeExecution 与 Checkpoint 均 fail-closed，不尝试自动迁移。

这个预算有两个明确非声明：

- Provider 请求已发出但响应/checkpoint 不确定时，恢复可能重试同一 graph task，不承诺真实 HTTP 请求
  Exactly Once；
- Deep Agents 0.7.8 的 `SummarizationMiddleware` 直接调用
  `_summary_model.with_retry()`，不经过主模型 middleware，最多可能额外尝试 3 次 Provider。7.0 不禁用
  原生压缩，真实 Smoke 必须单 Turn 且不触发 summarization，因此当前不是覆盖所有 Provider 请求的费用
  硬上限。

## Checkpointer 并发边界

本地 `langgraph-checkpoint-postgres==3.1.1` 的 `AsyncPostgresSaver` 实例包含异步锁，单实例协程访问在
correctness 上安全；但锁会串行化该实例的 checkpoint I/O。7.0 按已确认决定继续使用单
`AsyncConnection` + singleton Saver，保留单连接故障面。仅给 singleton Saver 换 pool 不会带来并行
收益；pool + per-execution Saver/graph factory 留到 7.1。

## 失败、恢复和取消

- 未知 backend、真实模式缺 Key、模型漂移或输出上限非法均在 Worker 启动前 fail-closed；
- Fake 模式不读取 Agent Provider Key，也不构造 `ChatDeepSeek` 或打开 Agent Checkpointer；
- Provider/graph 异常继续归一化为 SDK-neutral 安全错误；嵌套在异步 graph `ExceptionGroup` 中的项目安全
  错误会被提取，预算耗尽不会被误报为 temporary Provider 故障；
- 模型清理使用 `try/finally`；异步 HTTP client 关闭失败时仍会关闭同步 client，再传播原始关闭错误；
- RuntimeExecution lease/fencing、同步 checkpoint、跨进程认领和取消边界沿用切片 6；7.0 没有改变
  Attempt、Event、Outbox 或业务提交协议。

## 重要测试和运行结果

2026-08-26 实际运行：

- TDD 红灯：缺少 Provider factory 时定向测试 collection 得到 `ModuleNotFoundError`；缺少 Worker runtime
  helper 时得到 `ImportError`；
- 配置、factory、Worker 与 Deep Adapter 合并离线测试：`64 passed in 2.40s`；覆盖默认 Fake、独立
  Secret、参数固定、未知配置拒绝、资源关闭、生产依赖组合、Provider 前额度拒绝及 checkpoint 恢复不
  返还额度；其中实际构造并关闭锁定的 `ChatDeepSeek`，但没有调用模型或网络；
- 加入本地开发脚本与浏览器 E2E 的 Fake/Secret 静态契约后：`67 passed in 2.48s`；
- 受影响 Application/Runtime control/Fake/Deep Adapter/Worker 回归：`83 passed in 63.13s`；本地
  Testcontainers PostgreSQL Checkpoint、RuntimeExecution 与真实双 OS 进程恢复：
  `3 passed in 16.57s`；最终完整非集成回归：`822 passed, 4 skipped in 75.44s`；
- `uv lock --check` 通过，完整 Ruff 通过，`pyright src` 为
  `0 errors, 0 warnings, 0 informations`；
- 没有使用真实 API Key，没有发起真实 Provider、网站、MCP 或 Sandbox 请求，也没有产生模型费用。
- 一次直接构造检查在宿主继承的 SOCKS 代理环境中因未安装 `socksio` 失败；清除同一组代理变量后实际
  构造为 `ChatDeepSeek deepseek-v4-flash 123 {'thinking': {'type': 'disabled'}}` 并正常关闭。项目不为此
  顺便新增 `socksio`；`dev.sh --real` 延续既有代理检测与清理逻辑。
- 主审补强旧 v1 RuntimeExecution/Checkpoint 拒绝、第二 Turn 覆盖预算 State 和异步关闭失败仍清理同步
  client 的测试；修正两项测试装配错误后，最终受影响定向回归为 `50 passed in 1.97s`。
- 主智能体独立验证：配置/factory/Deep Adapter/Runtime control/Worker/dev/e2e 定向回归
  `75 passed in 2.63s`；PostgreSQL Checkpoint、RuntimeExecution control 与真实双 OS 进程恢复
  `3 passed in 15.90s`；完整非集成回归 `824 passed, 4 skipped in 74.95s`；
- 主智能体独立执行 `ruff check src tests` 通过，`pyright src` 为
  `0 errors, 0 warnings`，`uv lock --check` 输出 `Resolved 228 packages`，
  `bash -n scripts/dev.sh web/e2e/run.sh` 与 `git diff --check` 均通过。
- 2026-08-30 thinking 调试配置与 4096 输出预算回归：最终定向测试 `122 passed`，排除
  Testcontainers 的完整后端回归 `1193 passed, 10 skipped`，Agent 两轮流程与 Usage PostgreSQL 集成
  `2 passed`；Ruff/Pyright 通过，未调用真实 Provider。

## 代码入口

- 配置：`backend/src/literature_agent/infrastructure/config.py`
- Provider factory：`backend/src/literature_agent/infrastructure/agent/deepseek_research_model.py`
- Deep Adapter 与预算：
  `backend/src/literature_agent/infrastructure/agent/deep_agents_research_agent_runtime.py`
- Worker 组合与生命周期：`backend/src/literature_agent/worker.py`
- 测试：`backend/tests/infrastructure/test_config.py`、
  `backend/tests/infrastructure/test_deepseek_research_model.py`、
  `backend/tests/infrastructure/test_deep_agents_research_agent_runtime.py`、`backend/tests/test_worker.py`

## 已知限制

- Slice 1 固定 Policy 仍为 `max_model_calls=1`、`max_tool_calls=0`、空 Tool allowlist；7.0 只证明无 Tool
  单 Turn enablement。7.1 前必须实现并验证服务端固定 Capability Profile，才能运行真实 Project Tool
  回路；
- 尚未执行显式真实 Provider Smoke，未验证 DeepSeek 实际响应、token usage 或流式质量；
- 主模型预算不覆盖 summarization 内部请求和 Provider 在途不确定窗口，也没有业务 Usage/费用账单闭环；
- singleton Saver 串行 checkpoint I/O，尚未验证多 Session 并发吞吐与单连接故障恢复；
- OpenSandbox、WorkspaceSnapshot、Browser、MCP 和 Skill 均属于后续独立切片。

## 60 秒面试说明

“我把真实 Deep Agents 模式放在 Worker 的 infrastructure composition root，而不是修改业务 Port。默认
配置仍返回完全离线 Fake；只有显式 deep_agents 才创建 thinking 受控且默认关闭的 ChatDeepSeek、持久
PostgreSQL Checkpointer、Project-scoped Context 和 RuntimeExecution lease 控制，Secret 不进入 repr、
Event 或业务状态。每轮主 Agent model call 在调用前把额度预留进同步 checkpoint，所以 Tool 失败恢复不
会重获额度；但 Deep Agents 的内部 summarization 会绕过这层且最多自行重试三次，我明确记录它不是完整
费用硬上限。当前 singleton Saver correctness 安全但 I/O 串行，后续以 per-execution saver factory 验证
并发，而不是把 SDK 状态当业务事实。”
